"""Bounded read-only Plex / Sonarr / Bazarr media diagnostics.

Source-only. No server access, services, or credentials are deployed by this
file. Only a trusted operator should run it with private environment values.
Never use Bazarr's providers/episodes GET: that endpoint starts a live search.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class Unavailable(Exception):
    pass


def require(ok, reason="source_unavailable"):
    if not ok:
        raise Unavailable(reason)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Unavailable("redirect_refused")


class Reader:
    def __init__(self, base, key, header="X-Api-Key"):
        u = urllib.parse.urlsplit(base)
        require(u.scheme in ("http", "https") and u.hostname and
                not u.username and not u.password and not u.query and
                not u.fragment and key and isinstance(key, str), "invalid_source")
        self.base = base.rstrip("/")
        self.key, self.header = key, header

    def get(self, path):
        require(path.startswith("/") and not path.startswith("//") and
                "\n" not in path and "\r" not in path, "invalid_path")
        req = urllib.request.Request(self.base + path, headers={
            self.header: self.key, "Accept": "application/json"})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as r:
                data = r.read(4 * 1024 * 1024 + 1)
            require(len(data) <= 4 * 1024 * 1024, "oversized_response")
            require(self.key.encode() not in data, "credential_echo")
            return json.loads(data)
        except (urllib.error.URLError, ValueError, TimeoutError, OSError):
            raise Unavailable("source_unavailable") from None


def listing(plex, section, kind):
    require(type(section) is int and section > 0 and kind in (1, 4), "invalid_library")
    rows = []
    total = None
    for start in range(0, 10000, 100):
        path = (f"/library/sections/{section}/all?type={kind}&includeGuids=1"
                f"&X-Plex-Container-Start={start}&X-Plex-Container-Size=100")
        data = plex.get(path)["MediaContainer"]
        batch = data.get("Metadata", [])
        require(isinstance(batch, list) and len(batch) <= 100 and
                type(data.get("totalSize")) is int and data["totalSize"] <= 10000 and
                data.get("offset", 0) == start and data.get("size") == len(batch) and
                (total is None or data["totalSize"] == total), "incomplete_plex_listing")
        total = data["totalSize"]
        rows += batch
        require(len(rows) <= total, "incomplete_plex_listing")
        if len(rows) == total:
            require(len(set(str(r["ratingKey"]) for r in rows)) == total, "duplicate_plex_key")
            return rows
        require(len(batch) == 100, "incomplete_plex_listing")
    raise Unavailable("plex_listing_limit")


def media_versions(detail):
    """Do not mistake multiple Plex Media entries for physical files."""
    media = detail.get("Media", [])
    require(isinstance(media, list), "invalid_media")
    versions = []
    for m in media:
        parts = m.get("Part", [])
        require(isinstance(parts, list), "invalid_media_parts")
        for p in parts:
            path = p.get("file")
            path_id = sha256(path.encode()).hexdigest()[:16] if isinstance(path, str) and path else None
            versions.append({
                "plex_media_id": m.get("id"), "part_id": p.get("id"),
                "file_ref": path_id,
                "file_name": PurePosixPath(path).name[:250] if path else None,
                "exists": p.get("exists"), "accessible": p.get("accessible"),
                "bitrate_kbps": m.get("bitrate"), "video_codec": m.get("videoCodec"),
                "audio_codec": m.get("audioCodec"),
                "height": m.get("height"), "width": m.get("width"),
                "container": m.get("container"), "size_bytes": p.get("size"),
            })
    present = {p["file_ref"] for p in versions if p["file_ref"] and
               p["exists"] is True and p["accessible"] is True}
    absent = sum(1 for p in versions if p["exists"] is False)
    if not versions:
        reason = "no_media_parts"
    elif any(p["exists"] not in (True, False) for p in versions):
        reason = "file_presence_unverified"
    elif len(present) == 1 and len(versions) > 1:
        reason = "multiple_metadata_entries_one_distinct_available_file"
    elif len(present) > 1:
        reason = "multiple_distinct_available_files"
    elif len(present) == 1:
        reason = "one_available_file"
    else:
        reason = "no_available_file"
    return {"reported_plex_parts": len(versions), "distinct_available_file_refs": len(present),
            "unavailable_parts": absent, "assessment": reason, "parts": versions}


def episode_candidates(sonarr, query, exact_id=None):
    """Latest imported episode suggestions. Never invent a Dexter franchise ID."""
    if exact_id is not None:
        require(type(exact_id) is int and exact_id > 0, "invalid_episode_id")
        e = sonarr.get(f"/api/v3/episode/{exact_id}")
        return [e]
    require(isinstance(query, str) and 1 <= len(query) <= 80, "invalid_query")
    series = sonarr.get("/api/v3/series")
    require(isinstance(series, list) and len(series) <= 10000, "sonarr_series_unavailable")
    matches = [s for s in series if query.casefold() in s.get("title", "").casefold()]
    require(len(matches) <= 20, "ambiguous_series_query")
    found = []
    for s in matches:
        rows = sonarr.get(f"/api/v3/episode?seriesId={s['id']}")
        require(isinstance(rows, list) and len(rows) <= 1500, "sonarr_episodes_unavailable")
        for e in rows:
            if e.get("hasFile") is True and e.get("episodeFileId"):
                e = dict(e)
                e["_series_title"] = s.get("title")
                e["_series_id"] = s.get("id")
                found.append(e)
    found.sort(key=lambda e: (e.get("episodeFile", {}).get("dateAdded", ""),
                              e.get("airDateUtc", ""), e.get("id", 0)), reverse=True)
    return found[:5]


def plex_episode_candidates(plex, tv_library_id, series_title, season, number):
    episodes = listing(plex, tv_library_id, 4)
    selected = [x for x in episodes if x.get("grandparentTitle", "").casefold() == series_title.casefold()
                and x.get("parentIndex") == season and x.get("index") == number]
    require(len(selected) <= 3, "ambiguous_plex_episode")
    details = []
    for item in selected:
        key = item.get("ratingKey")
        require(str(key).isdigit(), "invalid_plex_key")
        v = plex.get(f"/library/metadata/{key}?checkFiles=1&includeGuids=1")["MediaContainer"]["Metadata"]
        require(isinstance(v, list) and len(v) == 1, "plex_details_unavailable")
        details.append({"rating_key": str(key), "files": media_versions(v[0])})
    return details


def bazarr_episode(bazarr, episode_id):
    # GET /api/providers/episodes deliberately excluded: it starts network searches.
    safe = {"status": "unknown", "subtitle_data": "unavailable"}
    try:
        d = bazarr.get(f"/api/episodes?episodeid%5B%5D={episode_id}")
        rows = d.get("data", [])
        require(isinstance(rows, list) and len(rows) <= 2, "bazarr_ambiguous")
        if len(rows) == 1 and rows[0].get("sonarrEpisodeId") == episode_id:
            e = rows[0]
            safe = {"status": "indexed", "scene_name": str(e.get("sceneName") or "")[:250],
                    "missing_subtitles": e.get("missing_subtitles"),
                    "subtitles": [{"language": x.get("name") or x.get("code2") or x.get("language"),
                                   "type": x.get("type"), "embedded": x.get("embedded")}
                                  for x in (e.get("subtitles") or [])[:30] if isinstance(x, dict)],
                    "audio_language": e.get("audio_language"),
                    "episode_monitored": e.get("monitored")}
        elif not rows:
            safe = {"status": "not_indexed", "reason": "bazarr_episode_missing"}
    except (Unavailable, ValueError, TypeError, AttributeError):
        pass
    return safe


def summarize(sonarr, plex, bazarr, *, query, movie_title, tv_library_id=2,
              movie_library_id=1, episode_id=None, movie_year=None):
    candidates = episode_candidates(sonarr, query, episode_id)
    episodes = []
    for e in candidates:
        sid = e.get("_series_id", e.get("seriesId"))
        s = e.get("_series_title")
        if not s and sid:
            s = sonarr.get(f"/api/v3/series/{sid}").get("title")
        if not isinstance(s, str) or not s:
            raise Unavailable("sonarr_series_title_unknown")
        files = plex_episode_candidates(plex, tv_library_id, s, e.get("seasonNumber"),
                                        e.get("episodeNumber"))
        baz = bazarr_episode(bazarr, e["id"]) if bazarr else {"status": "source_unavailable"}
        episodes.append({"series": s, "season": e.get("seasonNumber"),
                         "episode": e.get("episodeNumber"), "sonarr_episode_id": e["id"],
                         "sonarr_file_id": e.get("episodeFileId"),
                         "imported_at": (e.get("episodeFile") or {}).get("dateAdded"),
                         "plex": files, "bazarr": baz})
    movie_rows = listing(plex, movie_library_id, 1)
    matches = [m for m in movie_rows if m.get("title", "").casefold() == movie_title.casefold()
               and (movie_year is None or m.get("year") == movie_year)]
    require(len(matches) <= 8, "ambiguous_movie_lookup")
    movies = []
    for m in matches:
        key = str(m.get("ratingKey", ""))
        require(key.isdecimal(), "invalid_movie_key")
        details = plex.get(f"/library/metadata/{key}?checkFiles=1")["MediaContainer"]["Metadata"]
        require(len(details) == 1, "movie_details_unavailable")
        movies.append({"title": m.get("title"), "year": m.get("year"), "rating_key": key,
                       "files": media_versions(details[0])})
    return {"contract_version": "0.1", "read_only": True,
            "status": "evidence_only", "playback_bitrate_type": "not_observed",
            "episode_candidates": episodes, "movie_candidates": movies,
            "interpretation": "Plex bitrate_kbps is file metadata; 25 vs 14 Mbps player label may represent required bandwidth or a quality selection. No playback session inferred.",
            "subtitle_limitation": "Bazarr provider candidate ranking is not captured by passive GET; manual provider search is not automatically authorized."}


def main():
    ap = argparse.ArgumentParser(description="Read-only media inspection; outputs PRIVATE sanitized JSON")
    ap.add_argument("--series", required=True)
    ap.add_argument("--movie", required=True)
    ap.add_argument("--episode-id", type=int)
    ap.add_argument("--movie-year", type=int)
    args = ap.parse_args()
    env = lambda key: os.environ[key]
    sonarr = Reader(env("SONARR_BASE_URL"), env("SONARR_API_KEY"))
    plex = Reader(env("PLEX_BASE_URL"), env("PLEX_TOKEN"), "X-Plex-Token")
    bazarr = Reader(env("BAZARR_BASE_URL"), env("BAZARR_API_KEY"), "X-API-KEY")
    print(json.dumps(summarize(sonarr, plex, bazarr, query=args.series, movie_title=args.movie,
                               episode_id=args.episode_id, movie_year=args.movie_year,
                               tv_library_id=int(env("PLEX_TV_LIBRARY_ID")),
                               movie_library_id=int(env("PLEX_MOVIES_LIBRARY_ID"))),
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"read_only": True, "status": "blocked",
                          "reason": "invalid_or_unavailable_source"}))
        sys.exit(2)
