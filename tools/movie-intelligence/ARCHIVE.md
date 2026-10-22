# Historical Watched Archive v0.1

This is a single-movie presentation operation, separate from acquisition and
Plex playback history. The existing `request_movie` and `reacquire_movie` APIs
are unchanged. No background service, scheduler, frontend or bulk import exists.

## Eligibility and ownership

- **media.db:** exactly one canonical TMDb/IMDb identity, with explicit lifetime
  `watched=true`. Monitoring is not watched evidence. Historical dates, including
  `legacy_placeholder` dates, are neither copied nor interpreted as Plex plays.
- **Radarr:** zero or one matching record; a managed film must have no file.
  Duplicate/conflicting IDs, file state and active/unknown queue entries block.
- **Plex Movies:** no playable copy. Missing GUIDs or uncertain availability fail
  closed. A retained unavailable item is acceptable only with explicit nonexistence.
- **Plex Watched:** presentation only, isolated from Movies/TV and acquisition
  roots, excluded from Seerr sync. Unknown/duplicate/foreign archive entries block.

Seerr's read-only movie endpoint corroborates title/year/TMDb/IMDb; current request
or availability conflicts also block. No Seerr mutation is exposed. The initial
Heretic pilot additionally requires existing Radarr ID 208, unmonitored/no-file.
The general contract may represent an unmanaged watched movie; monitoring alone
never establishes historical eligibility.

## API

[archive.py](archive.py) uses Python's standard library and trusted injected
read APIs, identity reader and synthetic renderer:

- `plan_archive_movie(tmdb_id)` performs read-only planning.
- `archive_movie(tmdb_id, execute=False)` is the default, equivalent to planning.
- `archive_movie(tmdb_id, execute=True, plan_fingerprint=reviewed_fingerprint)`
  is **mutating** and requires the identity in the trusted execution allowlist.

Deployment configuration supplies exact archive/main library IDs, archive name,
Plex root, canonical host root, existing private state directory, forbidden host
media roots, execution allowlist, and optional expected Radarr IDs. Credentials
are injected privately; there is no credential file, argument or default secret.
Do not expose configuration/dependencies as user request fields.

The normalized SQLite reader opens `mode=ro`, enables query-only mode and reads
only canonical identity plus watched state. Existing WAL coordination files may
be read by SQLite; no database schema, content or journal-mode writes are issued.

## File and operation safety

The deterministic path is `<archive-root>/<safe title> (<year>) {tmdb-<id>}/`
with the same basename and `.mp4`. A stable private manifest is keyed by TMDb ID
and records IMDb, title/year, library, exact paths, payload hash/size, stages and
verified Plex ratingKey. Title/path drift requires review, not a second item.

Execution exclusively creates a durable manifest before work. It refreshes
eligibility inside the reservation and immediately before publishing. FFmpeg/ffprobe
run in disposable containers from the existing digest-pinned Bazarr image, with
no network, read-only filesystem, dropped capabilities and bounded resources.
They never execute inside the running Bazarr service. The existing host DejaVu Sans
font is hash-verified and mounted read-only at an explicit `fontfile` path; missing
or changed fonts fail closed. No implicit font discovery or image pull is used:
60-second 640x360 H.264/yuv420p, no audio, with visible `ARCHIVE PLACEHOLDER`,
`NOT THE ACTUAL FILM` and movie title/year. No copyrighted footage or service API
is used. No software installation or service change is required.

Before live execution, run `render_synthetic` with the actual host Docker runtime.
The returned MP4 must pass ffprobe, including dimensions, codec, no audio and
duration. Offline mocks alone do not establish renderer runtime acceptance.
Do not retry a reserved operation automatically. A pre-publication recovery needs
explicit inspection of the receipt, both file paths, archive metadata and retained
failure snapshots. Preserve the original receipt and evidence under a new private
recovery directory before freeing this one identity for a fresh reviewed plan.
Any uncertain publication or scan outcome blocks recovery.

The encoder writes an ordinary `+faststart` MP4 into a private temporary directory,
then ffprobe reads the seekable file. This avoids first-fragment-only duration
reporting from fragmented MP4 over a pipe. The directory is not a Plex media root
and is removed after validation. The renderer returns a validated, at-most-2-MiB MP4
in memory. It is persisted in
the private directory, then atomically hardlinked into the archive using exclusive
directory/link creation. State and archive must share a filesystem. Never overwrite
or adopt a pre-existing directory/file. A changed hash, symlink, scope or identity
blocks. Private state is mode 0700; manifests/payload are 0600 and must be readable
by the existing Plex service UID. Provision directory ownership before invocation.

Only `GET /library/sections/<archive-id>/refresh?path=<exact archive movie folder>`
is used to request scanning. This GET **is mutating** and exists only behind the
execution gate. No whole-library refresh, Match, scrobble, unscrobble or trash
operation is exposed. Metadata matching must succeed naturally from the canonical
filename. Read-back checks exact TMDb/IMDb/year/title, file path/accessibility,
poster response and absence of playback history. Indexing is polled for a bounded
window; a delayed/failed/uncertain scan is not automatically repeated.

Successful repetition verifies the owned manifest, file hash and indexed item,
then returns `already_archived` without generating or scanning again. A crash,
partial file, uncertain scan or unmatched item stops for manual reconciliation.
There is no automatic cleanup, lock expiry, overwrite, resume-from-render or retry.
If a delayed index eventually satisfies verification, later read-only planning can
recognize the item without another scan. Receipts and files are retained.

There is no cross-application transaction. Avoid competing library imports or
manual edits during the pilot; the last prepublish GET cannot lock external apps.

## Tests and live boundary

Run from this directory on Linux:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s test -p test_archive.py
```

25 offline tests cover eligibility/provenance, canonical conflicts, file presence,
archive conflicts, scope/permissions, explicit execution, stale plans, queue and
Seerr guards, prepublish drift, uncertain scan, pending indexing, wrong metadata,
hash changes, restart/repetition, concurrent reservation and renderer arguments.
Tests use synthetic fixtures and never contact live apps. Existing Node tests and
reacquisition code are unchanged. Live outcome belongs in the Media Project State.

For the owner-authorized pilot, set the execution allowlist to **Heretic only**,
TMDb 1138194, and expected Radarr ID 208. Revalidate watched=true, unmonitored,
no-file, identity, real Movies absence and Seerr exclusion immediately before
creation. Preserve before/after inventories, Radarr snapshots, SQLite DB/WAL hashes
and Zodiac file hash. Verify Seerr does not report the placeholder Available.
Samsung acceptance still requires user confirmation; server verification is not
client acceptance.

## Rollback and future lifecycle (not implemented)

Rollback is manual and separately authorized. Use the manifest's exact newly
created file/directory, private payload/receipt and indexed ratingKey. Reverify
library/IDs/hash before removing only the new synthetic item. The public API has
no delete method; do not empty trash or delete an existing real movie as rollback.
Preserve the Zodiac item and the shared archive library. Metadata cleanup must be
scoped separately because filesystem removal alone can leave a Plex trash item.

Future states:

| State | Required reality | Future action |
| --- | --- | --- |
| ARCHIVED | Historical watched=true; no real movie file | One archive placeholder |
| ACTIVE | Real import and accessible matching Movies item verified | Remove only the owned synthetic placeholder after verification |
| RETURN TO ARCHIVE | User removed real file; historical watched remains true | Restore one placeholder after fresh eligibility checks |

No transition is automated in v0.1. Never delete a real movie file, infer watched
state from Radarr monitoring, or synthesize Plex history. Future lifecycle work
must distinguish owned placeholders from real media and retain identity/receipts.
