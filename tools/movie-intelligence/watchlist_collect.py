"""Source-only read collector for the existing Movie Intelligence #26 audit.

This module has no write operations. A trusted operator supplies approved
service clients and a fixed private SQLite path. No runtime, service or
credential permission is granted by importing/running this code.
Only the aggregate is printed by the optional CLI. Personal rows are returned
to the caller in memory and must never be published to a public GitHub repo.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

from lifecycle_cli import plex_listing
from watchlist_inventory import (
    InventoryUncertain, classify_watchlist, diagnose_watchlist_identities,
)

MAX_RECORDS = 10000
MAX_DB_JOIN_ROWS = 40000
MAX_REQUESTS = 1000
MAX_COLLECTION_SECONDS = 150


def _unavailable():
    raise InventoryUncertain("watchlist_inventory_unavailable")


def _id(value):
    if type(value) is bool:
        _unavailable()
    if value in (None, "", 0):
        return None
    if type(value) is int and value > 0:
        return value
    if type(value) is str and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    _unavailable()


def _imdb(value):
    if value in (None, ""):
        return None
    if type(value) is not str or re.fullmatch(r"tt[0-9]{7,10}", value) is None:
        _unavailable()
    return value


def _flag(value):
    if type(value) is int and value in (0, 1):
        return bool(value)
    _unavailable()


def normalized_catalog(db_path):
    """Consistent single-transaction SQLite WAL read; never assume missing = false."""
    if type(db_path) is not str or not db_path or not Path(db_path).is_absolute():
        _unavailable()
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True,
                               timeout=3.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            rows = conn.execute("""
                SELECT m.id, m.title, m.year, u.movie_id, u.watched, u.watchlist,
                       e.source, e.external_id
                FROM movies m
                LEFT JOIN user_movies u ON u.movie_id=m.id
                LEFT JOIN external_ids e ON e.movie_id=m.id
                ORDER BY m.id, e.source
                LIMIT 40001
            """).fetchall()
            if len(rows) > MAX_DB_JOIN_ROWS:
                _unavailable()
            movies, owners = {}, set()
            for mid, title, year, owner_id, watched, watchlist, source, external in rows:
                if (type(mid) is not int or mid <= 0 or
                        type(owner_id) is not int or owner_id != mid or
                        type(title) is not str or not title.strip() or
                        year is not None and (type(year) is not int or not 1880 <= year <= 2200)):
                    _unavailable()
                if mid not in movies:
                    if len(movies) >= MAX_RECORDS:
                        _unavailable()
                    movies[mid] = {
                        "id": mid, "title": title, "year": year,
                        "watched": _flag(watched), "watchlist": _flag(watchlist),
                        "tmdb": None, "imdb": None,
                    }
                else:
                    m = movies[mid]
                    if (m["title"] != title or m["year"] != year
                            or m["watched"] != _flag(watched)
                            or m["watchlist"] != _flag(watchlist)):
                        _unavailable()
                if source in ("tmdb", "imdb"):
                    if type(external) is not str:
                        _unavailable()
                    value = _id(external) if source == "tmdb" else _imdb(external)
                    if value is None or movies[mid][source] is not None:
                        _unavailable()
                    identity = (source, value)
                    if identity in owners:
                        _unavailable()
                    owners.add(identity)
                    movies[mid][source] = value
                elif source is not None and source != "csfd":
                    # Other namespace rows do not establish a safe canonical match.
                    continue
            conn.execute("ROLLBACK")
            return list(movies.values())
        finally:
            conn.close()
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError):
        _unavailable()


def plex_movies(plex, *, movies_id, archive_id):
    """List only real Movies, never synthetic historical Watched placeholders."""
    if (type(movies_id) is not int or type(archive_id) is not int
            or movies_id < 1 or archive_id < 1 or movies_id == archive_id):
        _unavailable()
    try:
        sections = plex.get("/library/sections")["MediaContainer"]["Directory"]
        if type(sections) is not list:
            _unavailable()
        main = [s for s in sections if str(s.get("key")) == str(movies_id)]
        archive = [s for s in sections if str(s.get("key")) == str(archive_id)]
        if (len(main) != 1 or main[0].get("title") != "Movies" or
                main[0].get("type") != "movie" or len(archive) != 1 or
                not archive[0].get("title", "").startswith("Watched") or
                archive[0].get("type") != "movie"):
            _unavailable()
        details = plex_listing(plex, movies_id)  # Exact per-item checkFiles=1 GET.
        if len(details) > MAX_RECORDS:
            _unavailable()
        rows = []
        for movie in details:
            guids = movie.get("Guid")
            if type(guids) is not list:
                _unavailable()
            ids = {"tmdb": None, "imdb": None}
            for entry in guids:
                if type(entry) is not dict or type(entry.get("id")) is not str:
                    _unavailable()
                value = entry["id"]
                for kind, pattern in (
                    ("tmdb", r"tmdb://([1-9][0-9]*)"),
                    ("imdb", r"imdb://(tt[0-9]{7,10})"),
                ):
                    match = re.fullmatch(pattern, value)
                    if match:
                        ident = int(match[1]) if kind == "tmdb" else match[1]
                        if ids[kind] is not None and ids[kind] != ident:
                            _unavailable()
                        ids[kind] = ident
            parts = []
            if type(movie.get("Media")) is not list:
                _unavailable()
            for media in movie["Media"]:
                if type(media) is not dict or type(media.get("Part")) is not list:
                    _unavailable()
                for p in media["Part"]:
                    if type(p) is not dict or type(p.get("exists")) is not bool or (
                        type(p.get("accessible")) is not bool
                    ):
                        _unavailable()
                    parts.append({"exists": p["exists"], "accessible": p["accessible"]})
            if not parts:
                _unavailable()
            rows.append({"rating_key": str(movie["ratingKey"]), **ids, "parts": parts})
        return rows
    except (KeyError, TypeError, ValueError, IndexError, InventoryUncertain):
        _unavailable()


def radarr_movies(radarr):
    """One full movie list and one bounded, complete Radarr queue GET."""
    try:
        raw_movies = radarr.get("/api/v3/movie")
        if type(raw_movies) is not list or len(raw_movies) > MAX_RECORDS:
            _unavailable()
        queue = radarr.get("/api/v3/queue?page=1&pageSize=1000&includeUnknownMovieItems=true")
        if (type(queue) is not dict or type(queue.get("totalRecords")) is not int or
                not 0 <= queue["totalRecords"] <= 1000 or queue.get("page") != 1
                or type(queue.get("records")) is not list or
                len(queue["records"]) != queue["totalRecords"]):
            _unavailable()
        queued = set()
        for row in queue["records"]:
            if type(row) is not dict:
                _unavailable()
            mid = _id(row.get("movieId"))
            if mid is None:
                # Unknown queue identity blocks a global "not requested" claim.
                _unavailable()
            queued.add(mid)
        result = []
        ids = set()
        for movie in raw_movies:
            if type(movie) is not dict:
                _unavailable()
            mid = _id(movie.get("id"))
            if mid is None or mid in ids:
                _unavailable()
            ids.add(mid)
            if (type(movie.get("monitored")) is not bool
                    or type(movie.get("hasFile")) is not bool):
                _unavailable()
            result.append({
                "id": mid, "tmdb": _id(movie.get("tmdbId")),
                "imdb": _imdb(movie.get("imdbId")),
                "has_file": movie["hasFile"], "monitored": movie["monitored"],
                "queued": mid in queued,
            })
        if queued - ids:
            _unavailable()
        return result
    except (KeyError, TypeError, ValueError, InventoryUncertain):
        _unavailable()


def seerr_movie_requests(seerr):
    """Read the complete movie request set via accepted Seerr 3.4.1 GET path."""
    seen_request_ids, identities = set(), set()
    result = []
    total = None
    try:
        for skip in range(0, MAX_REQUESTS + 1, 100):
            response = seerr.get(
                f"/api/v1/request?take=100&skip={skip}&filter=all&mediaType=movie"
            )
            if type(response) is not dict or type(response.get("pageInfo")) is not dict:
                _unavailable()
            expected = response["pageInfo"].get("results")
            rows = response.get("results")
            service_errors = response.get("serviceErrors")
            if (type(expected) is not int or not 0 <= expected <= MAX_REQUESTS or
                    total is not None and expected != total or type(rows) is not list
                    or len(rows) > 100 or type(service_errors) is not dict or
                    type(service_errors.get("radarr")) is not list or service_errors["radarr"]):
                _unavailable()
            total = expected
            for item in rows:
                if type(item) is not dict or item.get("type") != "movie" or (
                    type(item.get("media")) is not dict
                    or item["media"].get("mediaType") != "movie"
                ):
                    _unavailable()
                request_id = _id(item.get("id"))
                tmdb = _id(item["media"].get("tmdbId"))
                if request_id is None or tmdb is None or request_id in seen_request_ids:
                    _unavailable()
                seen_request_ids.add(request_id)
                if tmdb not in identities:
                    identities.add(tmdb)
                    result.append({"tmdb": tmdb, "imdb": None, "requested": True})
            if len(seen_request_ids) == total:
                return result
            if len(rows) != 100 or len(seen_request_ids) > total:
                _unavailable()
        _unavailable()
    except (KeyError, TypeError, ValueError, InventoryUncertain):
        _unavailable()


def collect(*, db_path, plex, radarr, seerr, movies_id, archive_id, clock=None, diagnose=False):
    """Collect one conservative complete snapshot, without public private title data."""
    started = time.monotonic()
    collected_at = (clock or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        catalog = normalized_catalog(db_path)
        movies = plex_movies(plex, movies_id=movies_id, archive_id=archive_id)
        managed = radarr_movies(radarr)
        requests = seerr_movie_requests(seerr)
        if time.monotonic() - started > MAX_COLLECTION_SECONDS:
            _unavailable()
        aggregate, private = classify_watchlist(
            catalog=catalog, plex_movies=movies, radarr_movies=managed,
            seerr_requests=requests,
            sources_complete={key: True for key in ("movie_db", "plex", "radarr", "seerr")},
            collected_at=collected_at,
        )
        if diagnose is True:
            detail = diagnose_watchlist_identities(
                catalog=catalog, plex_movies=movies, radarr_movies=managed,
                seerr_requests=requests, private_items=private,
            )
            return aggregate, private, detail
        if diagnose is not False:
            _unavailable()
        return aggregate, private
    except Exception:
        # Never forward raw HTTP errors, paths, private titles or database content.
        _unavailable()


def main():
    """Operator-only transient command; no deployment, scheduler or file publication."""
    from archive import ReadAPI

    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnose-identities", action="store_true")
    args = parser.parse_args()
    try:
        env = os.environ
        result = collect(
            db_path=env["MOVIE_DB_PATH"],
            plex=ReadAPI(env["PLEX_BASE_URL"], env["PLEX_TOKEN"], "X-Plex-Token"),
            radarr=ReadAPI(env["RADARR_BASE_URL"], env["RADARR_API_KEY"]),
            seerr=ReadAPI(env["SEERR_BASE_URL"], env["SEERR_API_KEY"]),
            movies_id=int(env["PLEX_MOVIES_LIBRARY_ID"]),
            archive_id=int(env["PLEX_ARCHIVE_LIBRARY_ID"]),
            diagnose=args.diagnose_identities,
        )
        if args.diagnose_identities:
            packet, _private_items, diagnostic = result
            output = {
                "read_only": True, "resource_id": "movies/watchlist-identity-diagnostic",
                "collected_at": packet["collected_at"], "diagnostic": diagnostic,
            }
        else:
            packet, _private_items = result
            output = packet
        print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))
        return 0
    except Exception:
        print(json.dumps({"read_only": True, "status": "blocked",
                          "reason": "watchlist_inventory_unavailable"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
