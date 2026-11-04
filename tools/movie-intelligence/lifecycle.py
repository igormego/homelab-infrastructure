"""Pure, fail-closed state planner for My Cinema archive/playable lifecycle.

No I/O, timers, Plex writes, ARR searches, metadata mutation or file deletion.
Live readers must supply *fully expanded* current identities and per-file existence.
The plan is evidence, never authorization to run its suggested action.
"""
from __future__ import annotations

import re


def _ids(item):
    guids = item.get("Guid")
    if not isinstance(guids, list) or not guids:
        raise ValueError("plex_canonical_identity_missing")
    out = {}
    for g in guids:
        val = g.get("id", "") if isinstance(g, dict) else ""
        for k, pattern in (("tmdb", r"tmdb://([1-9][0-9]*)"), ("imdb", r"imdb://(tt[0-9]{7,10})")):
            m = re.fullmatch(pattern, val)
            if m:
                value = int(m[1]) if k == "tmdb" else m[1]
                if k in out and out[k] != value:
                    raise ValueError("plex_duplicate_identity")
                out[k] = value
    if not out:
        raise ValueError("plex_canonical_identity_missing")
    return out


def _parts(item):
    media = item.get("Media")
    if not isinstance(media, list):
        raise ValueError("plex_file_evidence_missing")
    parts = []
    for m in media:
        if not isinstance(m, dict) or not isinstance(m.get("Part"), list):
            raise ValueError("plex_file_evidence_missing")
        parts += m["Part"]
    if not parts:
        raise ValueError("plex_file_evidence_missing")
    for p in parts:
        if not isinstance(p, dict) or type(p.get("exists")) is not bool:
            raise ValueError("plex_file_evidence_missing")
    return parts


def _matches(rows, identity):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("plex_listing_unavailable")
    found = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or str(row.get("ratingKey", "")).strip() == "":
            raise ValueError("plex_listing_unavailable")
        key = str(row["ratingKey"])
        if key in seen:
            raise ValueError("plex_duplicate_rating_key")
        seen.add(key)
        ids = _ids(row)  # Unknown identity anywhere blocks proof of target absence.
        if ids.get("tmdb") == identity["tmdb"] or ids.get("imdb") == identity["imdb"]:
            if any(ids[k] != identity[k] for k in ids):
                raise ValueError("plex_identity_conflict")
            found.append(row)
    if len(found) > 1:
        raise ValueError("plex_target_duplicate")
    return found


def _radarr(rows, identity):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("radarr_listing_unavailable")
    found = [m for m in rows if
             m.get("tmdbId") == identity["tmdb"] or m.get("imdbId") == identity["imdb"]]
    if len(found) > 1:
        raise ValueError("radarr_duplicate_movie")
    if not found:
        return None
    m = found[0]
    if m.get("tmdbId") != identity["tmdb"] or (
            m.get("imdbId") not in (None, "", identity["imdb"])):
        raise ValueError("radarr_identity_conflict")
    if type(m.get("monitored")) is not bool or type(m.get("hasFile")) is not bool:
        raise ValueError("radarr_state_unknown")
    if m["hasFile"] is False and (m.get("movieFileId") or m.get("sizeOnDisk")):
        raise ValueError("radarr_file_conflict")
    return m


def plan_lifecycle(identity, radarr_movies, plex_movies, plex_archive, archive_receipt=None):
    """Return one bounded plan; deliberately no mutation or retries.

    archive_receipt may be a verified private item receipt, not a downloaded
    title list. Full receipt/hash validation is the writer's responsibility.
    """
    public_id = {"tmdb": identity.get("tmdb"), "imdb": identity.get("imdb")}
    result = lambda state, action, reason: {
        "decision": state, "action": action, "reason": reason,
        "identity": public_id, "mutation_attempted": False,
    }
    try:
        if type(identity.get("tmdb")) is not int or identity["tmdb"] <= 0 or (
                not isinstance(identity.get("imdb"), str) or
                not re.fullmatch(r"tt[0-9]{7,10}", identity["imdb"])):
            return result("needs_review", "none", "invalid_canonical_identity")
        if identity.get("watched") is not True:
            return result("not_eligible", "none", "media_db_lifetime_watched_not_true")
        radarr = _radarr(radarr_movies, identity)
        active = _matches(plex_movies, identity)
        archived = _matches(plex_archive, identity)
        has_real_file = bool(active and any(
            p["exists"] is True and p.get("accessible") is True for p in _parts(active[0])
        ))
        if active and any(p["exists"] is True and p.get("accessible") is not True
                          for p in _parts(active[0])):
            raise ValueError("plex_real_file_unavailable")
        if radarr and radarr["hasFile"] and not has_real_file:
            return result("transition_pending", "none", "radarr_file_not_yet_playable_in_plex")
        if radarr and not radarr["hasFile"] and has_real_file:
            return result("needs_review", "none", "radarr_plex_file_conflict")
        if archived:
            parts = _parts(archived[0])
            if not all(p["exists"] is True and p.get("accessible") is True for p in parts):
                return result("needs_review", "none", "archive_file_unavailable")
            if not isinstance(archive_receipt, dict) or (
                    archive_receipt.get("stage") != "verified" or
                    str((archive_receipt.get("verified") or {}).get("rating_key", "")) != str(archived[0]["ratingKey"]) or
                    (archive_receipt.get("identity") or {}).get("tmdb") != identity["tmdb"]):
                return result("needs_review", "none", "archive_ownership_not_verified")
        elif archive_receipt is not None:
            return result("needs_review", "none", "orphaned_archive_receipt")
        if has_real_file:
            if archived:
                # Removal needs a separately accepted Plex per-item metadata cleanup
                # plus an archive receipt retirement/rearm protocol.
                return result("active", "retire_archive_review", "real_file_verified_with_owned_archive_copy")
            return result("active", "none", "real_file_verified")
        if radarr and radarr["monitored"]:
            return result("transition_pending", "none", "radarr_monitored_without_file")
        if archived:
            return result("archived", "none", "owned_archive_placeholder_verified")
        return result("archive_eligible", "plan_archive", "historical_watched_with_no_real_file")
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        allowed = {"plex_canonical_identity_missing", "plex_duplicate_identity",
                   "plex_file_evidence_missing", "plex_listing_unavailable",
                   "plex_duplicate_rating_key", "plex_identity_conflict",
                   "plex_target_duplicate", "radarr_listing_unavailable",
                   "radarr_duplicate_movie", "radarr_identity_conflict",
                   "radarr_state_unknown", "radarr_file_conflict",
                   "plex_real_file_unavailable"}
        return result("needs_review", "none", str(e) if str(e) in allowed else "source_evidence_unavailable")
