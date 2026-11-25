"""Bounded and replay-safe multi-film driver for the accepted single-film Archive.

No retries, no direct Plex/ARR writes, and no background scheduling. The
injected Archive controller remains the only mutation authority. An explicit
operator-reviewed digest is required for every bounded batch execution.
"""
import hashlib
import json


TARGET_REVIEW_REASONS = frozenset({
    "historical_watched_not_unique_or_true",
    "invalid_tmdb_identity",
    "imdb_identity_required",
    "ambiguous_imdb",
    "canonical_identity_conflict",
    "invalid_title",
    "seerr_conflict",
    "ambiguous_radarr",
    "radarr_file_or_identity_conflict",
    "playable_or_unknown_main_movie",
    "duplicate_plex_movie",
    "plex_identity_conflict",
    "unowned_archive_conflict",
    "watch_history_count_conflict",
})


def _ids(movie_ids, max_movies):
    if type(max_movies) is not int or not 1 <= max_movies <= 10:
        raise ValueError("invalid_batch_limit")
    if type(movie_ids) is not list or not 1 <= len(movie_ids) <= max_movies:
        raise ValueError("invalid_batch")
    if any(type(i) is not int or i <= 0 for i in movie_ids) or len(set(movie_ids)) != len(movie_ids):
        raise ValueError("invalid_movie_ids")
    if movie_ids != sorted(movie_ids):
        raise ValueError("batch_ids_must_be_sorted")
    return movie_ids


def _digest(records):
    # No paths/tokens or mutable upstream timestamps in the digest.
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plan_record(archive, tmdb):
    plan = archive.plan_archive_movie(tmdb)
    if plan.get("mutation_attempted") is not False or plan.get("decision") not in (
        "eligible", "already_archived", "blocked", "needs_review"
    ):
        return None, "invalid_upstream_plan"
    decision = plan["decision"]
    fingerprint = plan.get("plan_fingerprint")
    if decision == "eligible" and (
        not isinstance(fingerprint, str) or len(fingerprint) != 64 or
        any(c not in "0123456789abcdef" for c in fingerprint)
    ):
        return None, "invalid_plan_fingerprint"
    if decision == "already_archived" and not plan.get("verified"):
        return None, "unverified_existing_archive"
    record = {"tmdb_id": tmdb, "decision": decision,
              "fingerprint": fingerprint if decision == "eligible" else None}
    return record, plan.get("reason")


def _ready_plan(records):
    return {"decision":"ready","reason":"bounded_plan_verified","mutation_attempted":False,
            "count":len(records),"records":records,"batch_fingerprint":_digest(records)}


def plan_archive_batch(archive, movie_ids, *, max_movies=5):
    """Return a read-only batch; one blocked/unknown movie stops the whole batch."""
    _ids(movie_ids, max_movies)
    records=[]
    for tmdb in movie_ids:
        record, reason = _plan_record(archive, tmdb)
        if record is None:
            return {"decision":"blocked","reason":reason,"mutation_attempted":False}
        records.append(record)
        if record["decision"] in ("blocked","needs_review"):
            return {"decision":"blocked","reason":"candidate_needs_review","blocked_tmdb":tmdb,
                    "mutation_attempted":False,"records":records}
    return _ready_plan(records)


def plan_next_archive_batch(archive, candidate_ids, *, max_movies=5):
    """Select a bounded, read-only batch and defer known title-specific problems."""
    if type(max_movies) is not int or not 1 <= max_movies <= 10:
        raise ValueError("invalid_batch_limit")
    if type(candidate_ids) is not list or len(candidate_ids) > 100:
        raise ValueError("invalid_candidate_list")
    if any(type(i) is not int or i <= 0 for i in candidate_ids) or (
        len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise ValueError("invalid_movie_ids")

    selected = []
    deferred = []
    for tmdb in candidate_ids:
        record, reason = _plan_record(archive, tmdb)
        if record and record["decision"] == "eligible":
            selected.append(record)
            if len(selected) == max_movies:
                break
        elif record and record["decision"] == "already_archived":
            continue
        elif record and isinstance(reason, str) and reason in TARGET_REVIEW_REASONS:
            deferred.append({"tmdb_id": tmdb, "reason": reason})
        else:
            return {"decision": "blocked", "reason": "shared_preflight_unavailable",
                    "blocked_tmdb": tmdb, "mutation_attempted": False, "deferred": deferred}

    if not selected:
        return {"decision": "no_ready_candidates", "mutation_attempted": False,
                "deferred": deferred}
    selected.sort(key=lambda record: record["tmdb_id"])
    return {**_ready_plan(selected), "deferred": deferred}


def execute_archive_batch(archive, movie_ids, *, reviewed_batch_fingerprint, max_movies=5, execute=False):
    """Run only a fresh matching plan; never retry an uncertain single-film result."""
    if type(execute) is not bool:
        return {"decision":"blocked","reason":"invalid_execute_flag","mutation_attempted":False}
    attempted=False
    completed=[]
    try:
        initial=plan_archive_batch(archive,movie_ids,max_movies=max_movies)
        if not execute:
            return initial
        if initial.get("decision")!="ready" or (
                not isinstance(reviewed_batch_fingerprint,str) or
                reviewed_batch_fingerprint!=initial["batch_fingerprint"]):
            return {"decision":"blocked","reason":"missing_or_stale_batch_approval","mutation_attempted":False}
        for row in initial["records"]:
            tmdb=row["tmdb_id"]
            # Every completed file was independently verified, no mutation needed.
            if row["decision"]=="already_archived":
                completed.append({"tmdb_id":tmdb,"decision":"already_archived"})
                continue
            latest=archive.plan_archive_movie(tmdb)
            if latest.get("decision")!="eligible" or latest.get("plan_fingerprint")!=row["fingerprint"]:
                return {"decision":"stopped","reason":"fresh_preflight_changed","mutation_attempted":attempted,
                        "completed":completed,"blocked_tmdb":tmdb}
            attempted=True  # An upstream exception after this point has uncertain effects.
            one=archive.archive_movie(tmdb,execute=True,plan_fingerprint=row["fingerprint"])
            completed.append({"tmdb_id":tmdb,"decision":one.get("decision","unknown")})
            if one.get("decision")!="archived":
                return {"decision":"stopped","reason":"single_movie_requires_reconciliation",
                        "mutation_attempted":True,"completed":completed,"blocked_tmdb":tmdb}
        return {"decision":"completed","reason":"each_movie_verified_by_archive_controller",
                "mutation_attempted":any(r["decision"]=="archived" for r in completed),
                "completed":completed}
    except Exception:
        return {"decision":"needs_review" if attempted else "blocked",
                "reason":"operation_uncertain_no_retry" if attempted else "source_or_batch_unavailable",
                "mutation_attempted":attempted,"completed":completed}
