"""Pure, fail-closed watchlist availability classifier for Movie Intelligence #26.

Consumes separately collected, complete *normalized* source inventories. No
network, SQLite, filesystem, Plex/ARR/Seerr APIs, writes or downloads happen here.
A trusted operator remains responsible for read-only source collection and for
keeping all personal per-title results PRIVATE. The aggregate is compatible with
PAB media.resource_read's source-only movies/watchlist-overview v0.1 schema.
"""
from __future__ import annotations

from collections import Counter
import re

MAX_ROWS = 10000
SOURCE_KEYS = ("movie_db", "plex", "radarr", "seerr")
DISPOSITIONS = ("playable", "managed_or_requested", "reviewable_missing", "unresolved")


class InventoryUncertain(ValueError):
    """Fixed external error. Never surface source data in an error string."""


def _deny():
    raise InventoryUncertain("watchlist_inventory_unavailable")


def _rows(rows, keys, id_key=None):
    if type(rows) is not list or len(rows) > MAX_ROWS:
        _deny()
    seen = set()
    ids = {"tmdb": set(), "imdb": set()}
    for row in rows:
        if type(row) is not dict or set(row) != set(keys):
            _deny()
        tmdb, imdb = row["tmdb"], row["imdb"]
        if (tmdb is not None and (type(tmdb) is not int or tmdb < 1)
                or imdb is not None and (
                    type(imdb) is not str or
                    re.fullmatch(r"tt[0-9]{7,10}", imdb) is None)):
            _deny()
        for kind, value in (("tmdb", tmdb), ("imdb", imdb)):
            if value is not None:
                if value in ids[kind]:
                    _deny()  # A source must not silently collapse duplicate IDs.
                ids[kind].add(value)
        if id_key is not None:
            value = row[id_key]
            if type(value) is not int or value < 1 or value in seen:
                _deny()
            seen.add(value)


def _matches(movie, source_rows):
    matching = []
    conflict = False
    for row in source_rows:
        shared = any(
            movie[k] is not None and row[k] is not None and movie[k] == row[k]
            for k in ("tmdb", "imdb")
        )
        if shared:
            if any(
                movie[k] is not None and row[k] is not None and movie[k] != row[k]
                for k in ("tmdb", "imdb")
            ):
                conflict = True
            matching.append(row)
    return matching, conflict or len(matching) > 1


def classify_watchlist(*, catalog, plex_movies, radarr_movies, seerr_requests,
                       sources_complete, collected_at):
    """Return (aggregate_snapshot, private_per_title_rows), without side effects.

    The source collector MUST prove these four inventories are complete, captured
    within a bounded window, and scoped to real main Plex Movies (no Watched
    placeholder section). No title/year matching is used.
    """
    if (type(sources_complete) is not dict
            or set(sources_complete) != set(SOURCE_KEYS)
            or any(sources_complete[k] is not True for k in SOURCE_KEYS)
            or type(collected_at) is not str
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", collected_at) is None):
        _deny()
    _rows(catalog, ("id", "title", "year", "watched", "watchlist", "tmdb", "imdb"), "id")
    _rows(plex_movies, ("rating_key", "tmdb", "imdb", "parts"))
    _rows(radarr_movies, ("id", "tmdb", "imdb", "has_file", "monitored", "queued"), "id")
    _rows(seerr_requests, ("tmdb", "imdb", "requested"))
    for row in catalog:
        if (type(row["title"]) is not str or not 1 <= len(row["title"]) <= 250
                or (row["year"] is not None and (type(row["year"]) is not int
                    or not 1880 <= row["year"] <= 2200))
                or type(row["watched"]) is not bool or type(row["watchlist"]) is not bool):
            _deny()
    for row in plex_movies:
        if (type(row["rating_key"]) is not str or re.fullmatch(r"[1-9][0-9]*", row["rating_key"]) is None
                or type(row["parts"]) is not list or not row["parts"]
                or len(row["parts"]) > 20 or any(
                    type(part) is not dict or set(part) != {"exists", "accessible"}
                    or type(part["exists"]) is not bool or type(part["accessible"]) is not bool
                    for part in row["parts"])):
            _deny()
    if len({row["rating_key"] for row in plex_movies}) != len(plex_movies):
        _deny()
    for row in radarr_movies:
        if any(type(row[k]) is not bool for k in ("has_file", "monitored", "queued")):
            _deny()
    for row in seerr_requests:
        if type(row["requested"]) is not bool:
            _deny()

    # Unknown identities in any source prevent a trustworthy absence claim.
    unknown_identity = any(
        row["tmdb"] is None and row["imdb"] is None
        for group in (plex_movies, radarr_movies, seerr_requests) for row in group
    )
    playable_count = sum(any(p["exists"] and p["accessible"] for p in m["parts"])
                         for m in plex_movies)
    summary = Counter()
    private_items = []
    for movie in catalog:
        if not movie["watchlist"]:
            continue
        decision = "unresolved"
        if movie["tmdb"] is not None or movie["imdb"] is not None:
            p, pc = _matches(movie, plex_movies)
            r, rc = _matches(movie, radarr_movies)
            s, sc = _matches(movie, seerr_requests)
            if not (pc or rc or sc):
                if p and any(part["exists"] and part["accessible"] for part in p[0]["parts"]):
                    decision = "playable"
                elif p:
                    # A retained Plex trash item is NOT a verified download.
                    decision = "unresolved"
                elif r or any(row["requested"] for row in s):
                    # Even an unmonitored/no-file Radarr record is still managed:
                    # never silently call a registered title "unmanaged".
                    decision = "managed_or_requested"
                elif not unknown_identity:
                    decision = "reviewable_missing"
        summary[decision] += 1
        # These rows belong only in the operator's private output, never GitHub.
        private_items.append({
            "title": movie["title"], "year": movie["year"],
            "tmdb": movie["tmdb"], "imdb": movie["imdb"],
            "disposition": decision,
        })
    catalog_count = {
        "total": len(catalog),
        "watched": sum(m["watched"] for m in catalog),
        "watchlist": len(private_items),
        "overlap": sum(m["watched"] and m["watchlist"] for m in catalog),
    }
    aggregate = {
        "schema_version": "0.1", "resource_id": "movies/watchlist-overview",
        "collected_at": collected_at, "complete": True,
        "source_status": {name: "ok" for name in SOURCE_KEYS},
        "catalog": catalog_count,
        "disposition": {key: summary[key] for key in DISPOSITIONS},
        "plex": {"playable_movies": playable_count},
    }
    assert sum(aggregate["disposition"].values()) == aggregate["catalog"]["watchlist"]
    return aggregate, private_items

def diagnose_watchlist_identities(*, catalog, plex_movies, radarr_movies,
                                   seerr_requests, private_items):
    """Bounded, aggregate-only explanation for unresolved classifications.

    This is a diagnostic sidecar, NOT the versioned PAB resource payload.
    Caller must supply the already validated source inventories and the
    per-title decisions from classify_watchlist. No IDs, names or paths escape.
    """
    watchlist = [movie for movie in catalog if movie["watchlist"] is True]
    if type(private_items) is not list or len(private_items) != len(watchlist):
        _deny()
    cover = Counter()
    reasons = Counter()
    unknown = {}
    for name, rows in (
        ("plex", plex_movies),
        ("radarr", radarr_movies),
        ("seerr", seerr_requests),
    ):
        unknown[name] = sum(
            row["tmdb"] is None and row["imdb"] is None for row in rows
        )
    global_guard = any(unknown.values())

    for movie, entry in zip(watchlist, private_items):
        if (type(entry) is not dict or
                entry.get("tmdb") != movie["tmdb"] or
                entry.get("imdb") != movie["imdb"] or
                entry.get("disposition") not in DISPOSITIONS):
            _deny()
        tmdb, imdb = movie["tmdb"] is not None, movie["imdb"] is not None
        coverage = (
            "both" if tmdb and imdb else
            "tmdb_only" if tmdb else
            "imdb_only" if imdb else
            "neither"
        )
        cover[coverage] += 1
        if entry["disposition"] != "unresolved":
            continue
        if coverage == "neither":
            reason = "no_tmdb_or_imdb"
        else:
            p, pc = _matches(movie, plex_movies)
            r, rc = _matches(movie, radarr_movies)
            s, sc = _matches(movie, seerr_requests)
            if pc or rc or sc:
                reason = "identity_conflict"
            elif p and not any(
                part["exists"] and part["accessible"] for part in p[0]["parts"]
            ):
                reason = "plex_unavailable"
            elif not p and not r and not any(x["requested"] for x in s) and global_guard:
                reason = "global_unknown_source_guard"
            else:
                reason = "other_unknown"
        reasons[reason] += 1

    unresolved = sum(r["disposition"] == "unresolved" for r in private_items)
    if sum(reasons.values()) != unresolved or sum(cover.values()) != len(watchlist):
        _deny()
    return {
        "diagnostic_version": "0.1",
        "watchlist_total": len(watchlist),
        "canonical_coverage": {
            k: cover[k] for k in ("neither", "tmdb_only", "imdb_only", "both")
        },
        "source_rows_without_canonical_identity": {
            k: unknown[k] for k in ("plex", "radarr", "seerr")
        },
        "unresolved_total": unresolved,
        "unresolved_reasons": {
            k: reasons[k] for k in (
                "no_tmdb_or_imdb", "identity_conflict", "plex_unavailable",
                "global_unknown_source_guard", "other_unknown",
            )
        },
    }
