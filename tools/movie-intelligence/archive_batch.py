"""Bounded and replay-safe multi-film driver for the accepted single-film Archive.

No retries, no direct Plex/ARR writes, and no background scheduling. The
injected Archive controller remains the only mutation authority. An explicit
operator-reviewed digest is required for every bounded batch execution.
"""
import hashlib
import json


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


def plan_archive_batch(archive, movie_ids, *, max_movies=5):
    """Return a read-only batch; one blocked/unknown movie stops the whole batch."""
    _ids(movie_ids, max_movies)
    records=[]
    for tmdb in movie_ids:
        p=archive.plan_archive_movie(tmdb)
        if p.get("mutation_attempted") is not False or p.get("decision") not in (
                "eligible", "already_archived", "blocked", "needs_review"):
            return {"decision":"blocked","reason":"invalid_upstream_plan","mutation_attempted":False}
        state=p["decision"]
        fp=p.get("plan_fingerprint")
        if state=="eligible" and (not isinstance(fp,str) or len(fp)!=64 or
                                  any(c not in "0123456789abcdef" for c in fp)):
            return {"decision":"blocked","reason":"invalid_plan_fingerprint","mutation_attempted":False}
        if state=="already_archived" and not p.get("verified"):
            return {"decision":"blocked","reason":"unverified_existing_archive","mutation_attempted":False}
        records.append({"tmdb_id":tmdb,"decision":state,"fingerprint":fp if state=="eligible" else None})
        if state in ("blocked","needs_review"):
            return {"decision":"blocked","reason":"candidate_needs_review","blocked_tmdb":tmdb,
                    "mutation_attempted":False,"records":records}
    return {"decision":"ready","reason":"bounded_plan_verified","mutation_attempted":False,
            "count":len(records),"records":records,"batch_fingerprint":_digest(records)}


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
