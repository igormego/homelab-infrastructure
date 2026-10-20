# Safe movie reacquisition v0.1

**Component-tested only, 2026-10-20. Not deployed or live-write-accepted.**
This explicit operation handles one existing Radarr movie that is unmonitored and
has no file. The existing [Seerr request controller](SEERR.md), including its
`already_managed` guard, is unchanged. Zodiac is absent from Radarr and is not a
candidate. Archive/Seerr isolation acceptance is reused, not retested.

## API and plan

`createMovieReacquisitionController` in [reacquire.mjs](reacquire.mjs) accepts trusted
deployment configuration: `radarr`, `seerr`, `plex`, `movieIntelligence` (the existing
read-only normalized adapter), and `journalDirectory`. Each HTTP configuration uses
`baseUrl`, `apiKey`, optional `timeoutMs` (1–60,000 ms), and optional trusted
`fetchImpl`. Plex also requires `moviesLibraryId`; the section must be named
`Movies` and have type `movie`. Credentials belong in private process memory, never
command arguments, repository files or output. No CLI or persistent service is added.

- `plan_reacquire_movie({tmdb_id, imdb_id?, expected_quality_profile_id?, expected_root_folder?})`
- `reacquire_movie(input, {execute: false})` is the default read-only plan.
- **Mutating, requires separate live authorization:**
  `reacquire_movie(input, {execute: true, plan_fingerprint: reviewedPlan.plan_fingerprint})`.

TMDb identity is mandatory; title-only resolution cannot authorize reacquisition.
A plan includes identity, exact API bodies, current profile/root/path/minimum
availability/tags, the sole movie-field change (`monitored: false → true`), and
rollback limits. Plans contain private library paths and must remain private.
The fingerprint binds the reviewed movie snapshot and complete quality profile.
Changed state requires a new plan, not silent acceptance.

## Selected write path

1. `PUT /api/v3/movie/editor` with only `{movieIds: [radarrId], monitored: true}`.
2. Re-read guards and compare every movie field except `monitored`, plus the entire
   quality profile, against the approved snapshot. Any difference stops before search.
3. `POST /api/v3/command` with only `{name: "MoviesSearch", movieIds: [radarrId]}`.
4. Read the returned command ID and verify its single movie target and status.

No full-movie PUT, Seerr POST, add, profile/root selection, tag merge, policy
override or library scan is permitted. Existing Radarr quality/download policy
governs the search. `submitted` means the single command was accepted, not that a
release was found, imported or playable.

Read-only host discovery confirmed Radarr **6.1.1.10360** and the required Radarr/Plex response field types. Its pinned source
[editor](https://github.com/Radarr/Radarr/blob/v6.1.1.10360/src/Radarr.Api.V3/Movies/MovieEditorController.cs)
and [resource](https://github.com/Radarr/Radarr/blob/v6.1.1.10360/src/Radarr.Api.V3/Movies/MovieEditorResource.cs)
apply only supplied nullable editor fields. The version guard rejects unreviewed
versions. Seerr **3.4.1** read-only checks reuse the existing bounded/admin-visible
client. Native Seerr re-request can apply its default profile/root/minimum
availability/tags, so it was not selected; see [ADR 004](../../docs/decisions/004-explicit-movie-reacquisition.md).

## Preflight and conservative blocks

Require exactly one Radarr identity, consistent available IMDb IDs across Radarr,
Movie Intelligence, Seerr and Plex, `hasFile=false`, no file ID/size, and
`monitored=false`. Require existing profile, accessible configured root, explicit
minimum availability/tags/path, and any supplied expected profile/root match.
The existing identity resolver rejects ambiguity and conflicting canonical IDs.

Read all bounded Radarr queue pages and Seerr request pages; incomplete or
inconsistent pages fail closed. Target/unknown queue entries, **any** existing
Seerr request (including old declined/failed ones), non-unknown Seerr availability
status (including cached availability), and any active Radarr command block.
This intentionally favors manual review over silently clearing history/cache.

Only the configured real Plex Movies section is read, with canonical GUIDs.
An existing matching item blocks unless every media part explicitly reports
non-existence via a read-only `checkFiles=1` metadata GET. Missing identity or
availability evidence, mismatched identities and duplicate matches block. The
archive placeholder library is never scanned or written by this operation.

Preflight runs before reservation, again inside it, and again after monitoring.
No multi-application transactional compare-and-swap exists: externally initiated
searches, requests or policy edits could race the last GET. All reacquisition
callers must share the same journal and canonical Radarr base URL; the live test
must exclude competing manual operations. This is not distributed exactly-once
delivery and does not lock the Radarr/Seerr UIs or RSS processing.

## Durable replay protection and rollback

Execution requires an existing private absolute `journalDirectory` on a local
filesystem, outside Git and separate from media.db/event ledgers. It must be
writable only by the service operator (private ACL on Windows / mode 0700 on
POSIX). Provisioning/deployment is not performed in this task. Do not use NFS,
multiple directories, URL aliases, or disposable storage for execution.

Exclusive file creation reserves each Radarr-server/TMDb identity across
controllers/processes. The receipt is flushed before the first write; each
attempt is appended/flushed before its HTTP call. There are no HTTP retries,
redirects, expired locks or automatic receipt deletion. Crashes, partial receipts,
failed reads and uncertain writes cannot authorize another search. A completed
receipt also prevents repeat execution after an external unmonitor operation.
Only one lifecycle per identity is supported in v0.1; a later lifecycle needs
separate reviewed reconciliation, not a new arbitrary idempotency key.

If monitoring succeeds but a later check fails, it may remain enabled. Report
`uncertain_requires_review`; inspect receipt, current monitoring, queue, commands
and history. Do not automatically unmonitor or retry. Unmonitoring alone cannot
undo a search/grab/download and must be separately authorized after reconciliation.
The wrapper never cancels downloads, deletes files or changes watched history.

## Validation and next live boundary

`node --test test/*.test.mjs`: **123 passing tests**, including all 60 pre-existing
tests unchanged and 63 new cases. Synthetic HTTP fixtures cover preservation,
identity conflicts, queue/request/cache blocks, Plex scope, API failures,
post-monitor drift, uncertain outcomes, restart/replay, two controllers and
exclusive reservation across two OS processes. No live request/search occurred.

Next: select one owner-approved non-Zodiac title already managed in Radarr with
exact TMDb/IMDb identity, unmonitored/no-file state, no queue/active command/Seerr
request or availability conflict, and no actual playable Movies item. Privately
provision the durable receipt directory and compatible Node runtime in a separately
authorized deployment. Review its fresh read-only plan and expected profile/root.
Then obtain separate authorization for **one** `execute:true` call and verify its
monitor/command/queue outcome. Do not infer live acceptance from component tests.
