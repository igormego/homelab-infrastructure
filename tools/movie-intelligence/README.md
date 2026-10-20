# Movie Intelligence read-only control plane v0.1

The separate [safe reacquisition operation](REACQUIRE.md) is component-tested for
already-managed, unmonitored, no-file movies. It does not weaken the existing
request controller's `already_managed` guard. No live reacquisition has occurred.

The read APIs below remain non-mutating. The separate [controlled Seerr movie-request wrapper](SEERR.md) adds an explicit opt-in Act boundary; it is component-tested and has not sent a live request.

**Component-tested locally; not deployed or live-accepted.** Implements [Issue #9](https://github.com/Az1mutt/homelab-infrastructure/issues/9). Requires Node.js **24.14+** with `node:sqlite`; no third-party packages, installation, server, Docker or shell wrapper. Tests passed on Node 24.19.0. The CLI reads personal data: keep its output private.

## Offline usage

From this directory:

```sh
node --test test/*.test.mjs
node cli.mjs --fixtures --title Heretik --year 2024
node cli.mjs --fixtures --tmdb-id 111
node cli.mjs --fixtures --title Scarface
```

Fixture mode ignores environment settings and never opens a database or makes network calls. Tests create disposable SQLite files and a loopback HTTP server; they never contact the homelab. Fixture names/IDs include the issue's public movie examples, but personal state, dates and other IDs are synthetic, not private exports.

## Later private configuration

| Environment variable | Purpose |
|---|---|
| `MOVIE_DB_PATH` | Existing SQLite file; no default host path and no file creation |
| `MOVIE_DB_SCHEMA_MODE` | `single-table` (backward-compatible default) or `normalized` (Issue #11 schema); never auto-detected |
| `MOVIE_DB_MAPPING_PATH` | Optional private JSON mapping described below |
| `RADARR_BASE_URL` | Radarr origin plus optional URL base, without `/api/v3`, credentials, query or fragment |
| `RADARR_API_KEY` | Secret supplied privately in the environment; never a CLI argument or committed file |

Once configured privately, `node cli.mjs --tmdb-id 111` reads both sources. Optional query flags: `--title`, `--year`, `--tmdb-id`, `--imdb-id`, `--csfd-id`. At least a title or ID is required; flags accept single values. Unknown flags/invalid input fail before source access. Exit 0 means both reads succeeded, **not** that identity is confirmed; inspect `summary.state`. Exit 2 means invalid input/configuration or a source error. Source failures produce partial JSON with `unresolved`; parser errors produce a fixed stderr message.

The module exports `get_movie_status(query, { movieIntelligence, radarr })`. Adapters are functions returning normalized record arrays (or promises), not URLs, SQL or shell commands. Production callers should use `readMovieIntelligence` and `readRadarr`. Built-in adapters validate fields and only expose allowlisted data. Exceptions are replaced by fixed source error codes, never upstream bodies, paths or stack traces.

## Normalized SQLite schema (Issue #11)

**Component-tested only:** `MOVIE_DB_SCHEMA_MODE=normalized` selects a fixed read adapter for the previously verified schema supplied in Issue #11. No homelab access, deployment, migration or schema change was performed. It reads `movies LEFT JOIN user_movies LEFT JOIN external_ids` in one statement, giving one consistent SQLite snapshot. It does not read `sync_runs`.

- `movies`: `id`, `title`, `original_title`, `year` supply movie identity.
- `user_movies`: `watched`/`watchlist` must be 0/1, `my_rating` becomes `personal_rating`, and `watched_at` is preserved. No user row produces null personal-state fields; actual default 0/0 remains false/false.
- `rated_at` stays null. Neither `watched_at`, `updated_at` nor `watchlist_added_at` is invented as a separate rating date. Bootstrap watchlist timestamps are not exposed as source-original dates.
- `external_ids`: exact lowercase `source` values `csfd`, `tmdb`, `imdb` pivot into contract IDs. Missing namespaces remain null. TMDb/ČSFD TEXT values must convert to positive safe integers; IMDb must be a valid `tt` identifier. Empty/malformed supported IDs fail the entire source with a fixed error. Unknown namespaces are ignored; no ID is inferred from URLs.
- Duplicate supported namespaces fail closed. Canonical IDs cannot belong to different movies, even if distinct TEXT values such as `111` and `0111` bypass the database's raw-text uniqueness. Cross-source conflicts still use the unchanged resolver.

The exact three-table fixture DDL is [normalized-schema.sql](fixtures/normalized-schema.sql), with the supplied keys, uniqueness and foreign keys. It is test setup, **not a deployment migration**. Only synthetic rows are inserted in disposable tests. The adapter retains read-only/defensive opening, disabled extensions, WAL visibility and the 100,000-movie limit; it also rejects more than 400,000 joined rows without truncating results. Identifier mapping is incompatible with normalized mode and is rejected; an unknown mode also fails explicitly.

Later authorized read command (POSIX shell, after privately setting `MOVIE_DB_PATH`, `RADARR_BASE_URL`, `RADARR_API_KEY` and unsetting `MOVIE_DB_MAPPING_PATH`):

```sh
MOVIE_DB_SCHEMA_MODE=normalized node cli.mjs --title Heretik --year 2024
MOVIE_DB_SCHEMA_MODE=normalized node cli.mjs --tmdb-id 111
```

These commands have **not** been run against the homelab. Module callers pass `schemaMode: 'normalized'` to `readMovieIntelligence`, or use the fixed `readNormalizedMovieIntelligence` export. There is no caller SQL argument.

## Single-table SQLite schema boundary

The original portable adapter remains available with `MOVIE_DB_SCHEMA_MODE=single-table` (default). Default supported table: `movies`. Required columns: positive integer `id`, nonempty text `title`. Optional columns: `original_title`, `year`, `tmdb_id`, `imdb_id`, `csfd_id`, `watched`, `watchlist`, `personal_rating`, `watched_at`, `rated_at`. Missing optional columns become `null`; unknown is never converted to false. Boolean values must be SQLite 0/1; ratings remain in the source scale; date strings are preserved, not guessed/reformatted. IDs are positive integers (IMDb uses `tt` plus digits). Invalid values fail the source rather than silently corrupt identity. Do not use this mode for the normalized live schema: select normalized mode explicitly.

For a different single-table schema, supply a JSON identifier mapping, for example:

```json
{
  "table": "films",
  "columns": {
    "id": "movie_id",
    "title": "name",
    "original_title": "original_name",
    "year": "release_year",
    "tmdb_id": "tmdb",
    "imdb_id": "imdb",
    "csfd_id": "csfd",
    "watched": "seen",
    "watchlist": "on_watchlist",
    "personal_rating": "rating",
    "watched_at": "seen_at",
    "rated_at": "rating_date"
  }
}
```

With an explicit mapping, omit or set `null` for unavailable optional fields; named but absent columns are an error. Only simple identifiers are accepted. No SQL configuration, SQL endpoint or migrations exist. The Issue #11 normalized schema uses the fixed adapter above, not identifier mapping; do not change production schema to fit this tool.

SQLite opens with `readOnly: true`, defensive mode, extensions disabled and a 3-second busy timeout. No journal-mode changes or `immutable` shortcut; committed WAL data remains visible. WAL readers can use/create coordination sidecars depending on SQLite/filesystem state, so this is no database mutation, not a claim of zero filesystem side effects. For literal no-write filesystem acceptance, use an appropriate read-only snapshot/mount. Never naively copy a live WAL database: use the established SQLite-aware backup workflow. The runtime does not create backups or change permissions.

## Contract and identity

Output shape (records omitted here):

```json
{
  "contract_version": "0.1",
  "query": { "title": null, "year": null, "external_ids": { "tmdb": 111, "imdb": null, "csfd": null } },
  "sources": {
    "movie_intelligence": { "status": "ok", "error": null, "candidates": [] },
    "radarr": { "status": "ok", "error": null, "candidates": [] }
  },
  "summary": { "state": "not_found", "reason": "no_candidates", "matched_by": null },
  "identity": { "external_ids": null, "enrichment": { "status": "not_requested" } }
}
```

| State | Meaning |
|---|---|
| `not_found` | Both sources successfully read; neither selected a candidate |
| `single_source_only` | Exactly one candidate in one source; no merged identity claim |
| `confirmed_match` | Exactly one per source with shared external ID and no ID/year conflict |
| `ambiguous` | Multiple candidates in a source, including duplicate stable IDs |
| `unresolved` | Source failure, conflicting query/source identity, or no shared stable ID |

TMDb takes precedence as the reported match basis, then IMDb, then ČSFD. **Any** disagreeing shared namespace vetoes a match even when TMDb agrees. Conflicting non-null years are conservative failures. Explicit ID queries select by any supplied ID, then check all supplied IDs for contradictions; they never substitute a title match for a missing ID. Title-only selection matches normalized exact title/original title, optionally year; no fuzzy substring matching. Stable IDs expand candidates across sources so translated names can join. Titles alone can suggest candidates but cannot confirm a cross-source match. Ambiguous candidates remain visible for user disambiguation.

Movie Intelligence candidates retain watched/watchlist/rating/dates and IDs. Radarr candidates retain ID/title/year/external IDs, monitored, has_file, quality_profile `{id,name}`, and status. File presence never implies watched state. Sources are read independently, not as an atomic cross-service transaction. `identity.external_ids` is populated only for a confirmed match; future TMDb enrichment can extend `identity.enrichment` without replacing source evidence or changing these states. No enrichment requests are implemented in v0.1.

## Radarr safety and limits

Only GET `/api/v3/movie` and `/api/v3/qualityprofile` are implemented. Authentication uses `X-Api-Key`; redirects are refused to prevent credential forwarding. Each request/body has a 10-second timeout (module configuration permits 1–60000 ms), a 32 MiB response limit, and a 100,000-record cap. SQLite uses the same record cap. Whole-library snapshots suit the current small library; no paging/truncation is silently treated as absence. Failure of either Radarr endpoint marks that source unavailable. Private media paths/download URLs and raw API responses are not returned. A credential echoed in an allowed response field also fails closed. Prefer trusted HTTPS or the existing private service network for later deployment; no network exposure is configured here.

## Later live acceptance (not performed by this change)

1. Review the Issue #11 schema contract and choose normalized mode; do not export personal rows or keys to Git.
2. With separate deployment authorization, place the tool on the intended host/runtime and configure secrets privately. No service installation is required.
3. Run Heretik by known identity/title and Scarface by TMDb 111; compare source-separated results with the existing prototypes. Confirm schema semantics, null handling, profile and file flags. Check the ČSFD timer journal where convenient under that separate live authorization.
4. Confirm no writes, grabs, request actions or configuration changes occurred. Store only sanitized evidence and mark live acceptance separately.
5. After live read acceptance, open a separate narrow **Seerr request wrapper** task. Direct unrestricted ARR mutation remains excluded.

No Seerr requests, downloads, upgrades, deletions, shell execution, arbitrary SQL or public API server are included. See [ADR 002](../../docs/decisions/002-movie-intelligence-read-contract.md), [Node SQLite](https://nodejs.org/docs/latest-v24.x/api/sqlite.html), and [Radarr API](https://radarr.video/docs/api/).
