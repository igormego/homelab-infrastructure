# My Cinema lifecycle — deterministic planner v0.1

The historical archive UI and the actual movie file inventory are separate
concepts. This module computes **what ought to happen**, without performing
filesystem, Plex, Seerr, Radarr, watch-state or download writes.

## Source-of-truth boundaries

- `media.db`: canonical lifetime Watched eligibility only. It is not a Plex
  watched flag, does not authorize a scrobble, and never controls file retention.
- `Radarr`: managed record, monitored state and file existence (a movie record
  can persist after deleting the video).
- `Plex Movies`: actual playable copy; require a *positive* canonical identity
  match and media parts with `exists=true` and `accessible=true`.
- `Plex Watched`: separate archived presentation. An item requires its exact
  private verified receipt, ratingKey and payload hash before being considered
  owned. Foreign, mismatched or unavailable items are never adopted.
- `Seerr`: remains excluded from scanning the Watched library; existing
  stale availability (e.g. Malignant) must not be overridden by this planner.

## Decisions and next actions

| State | Meaning | Planner action |
| --- | --- | --- |
| `not_eligible` | `media.db` not lifetime watched | None |
| `archive_eligible` | Watched, no real file, no archive copy, no pending acquisition | `plan_archive` (not a write permit) |
| `archived` | Owned placeholder present; no real copy | None |
| `active` | Playable Movies item exists | None |
| `active` + owned archive | Real movie and placeholder coexist | `retire_archive_review` (not executable) |
| `transition_pending` | Monitored missing or Radarr file before Plex import | None |
| `needs_review` | Ambiguous identity/file/receipt, conflicting state | None |

An unidentifiable item anywhere in the real Movies listing blocks proof of
absence, matching the accepted Peaky Blinders guard. Missing/ambiguous metadata
and uncertain file existence fail closed. A retained unavailable Plex trash
item may coexist with an eligible archive candidate but is **not** a durable
archive and is never automatically deleted.

## Read-only inventory CLI

`lifecycle_cli.py` consumes existing normalized SQLite `media.db`, Radarr
GET and Plex GET only. It enumerates lifetime watched IDs with strict
canonical identity uniqueness. Plex full listings and exact per-item
`checkFiles=1` details are read; no inference from item counts, Radarr
`unmonitored` alone or a stale Seerr label.

Use existing private environment variables:

```sh
# Read-only, contains private movie history in stdout; do not publish output.
MOVIE_DB_PATH=<existing-private-db> \
PLEX_BASE_URL=<existing-local-plex-base> PLEX_TOKEN=<private-token> \
PLEX_MOVIES_LIBRARY_ID=1 PLEX_ARCHIVE_LIBRARY_ID=4 \
RADARR_BASE_URL=<existing-local-radarr-base> RADARR_API_KEY=<private-key> \
MY_CINEMA_ARCHIVE_STATE_DIR=<private-archive-receipt-dir> \
python3 tools/movie-intelligence/lifecycle_cli.py --limit 25
```

Environment values above are placeholders, not runnable credentials. Keep
private paths/tokens outside GitHub. `MY_CINEMA_ARCHIVE_STATE_DIR` is optional:
without it, existing archive items conservatively return `needs_review`.
Complete receipts must live on a persistent local filesystem in a private 0700
directory, with regular 0600 files and matching published file SHA-256.
The output contains only a bounded sample of personal titles, plus aggregate
counts; it never writes a snapshot file.

## Explicitly NOT implemented

This source-only planner **does not** retire Plex items or delete their
synthetic placeholder files. Automatic lifecycle requires a separately
accepted, per-item Plex metadata removal path that does not empty unrelated
trash and an append-only archive receipt rearm mechanism for repeated
`ARCHIVED → ACTIVE → ARCHIVED` cycles. Simply unlinking the MP4 is
insufficient when Plex retains unavailable trash items. Do not silently change
`autoEmptyTrash` or run global trash cleanup.

It also does not bulk-create historical placeholders. The accepted
`archive_movie` operation can create one eligible movie with explicit allowlist
and fingerprint; large migrations need bounded batches, durable cursors and
fresh cross-source checks. No movie is auto-downloaded by inventory.

The owner-only request UI is documented in [MY-CINEMA-UI.md](MY-CINEMA-UI.md).
It is a companion app (not an injected Samsung Plex button), and it remains
source-only until a separately scoped host acceptance.

## Offline verification

From `tools/movie-intelligence`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s test -p 'test_lifecycle*.py'
```

The project GitHub Actions Movie Intelligence workflow runs these tests
alongside the existing Node suite. No live homelab operation is part of CI.
