# SerialZone personal TV sync (issue #66)

**Source implementation only.** No host migration, canonical identity merge or timer activation is implied.

The importer collects five public profile lists, checks section totals and bounded pagination, then writes a current source-specific projection inside the existing media.db. Series URLs are source locators, not verified canonical IDs. Only ratings-page scores are personal ratings; other displayed ratings are not imported as personal values. No raw HTML, actual personal rows or profile URL belongs in Git.

A separate, deterministic personal data client uses its declared user agent, checks robots.txt at runtime, allows only the fixed HTTPS origin, refuses redirects and stops on errors. Do not use it to bypass a denial; use a permitted export if needed. Terms: https://www.serialzone.cz/web/tos/. No AI scraping or training.

## Operator acceptance

Use the existing authorized local Homelab operator lane. Verify the current DB path, schema, runtime account and existing ingest schedule, review the two proposed additive source tables with the Infrastructure owner, and keep the runtime private. The importer does not touch canonical tables or the existing event ledger. A private SQLite backup is mandatory before initial table creation. Existing scheduled backup policy remains authoritative thereafter.

1. Set private SERIALZONE_PROFILE_URL and MOVIE_DB_PATH in the operator environment, never commit them.
2. Run `python3 serialzone_sync.py` (collection only, aggregate output). Compare counts with the actual profile and ensure all five lists and ratings pagination are complete.
3. After schema acceptance, create a private backup directory (0700) and run `python3 serialzone_sync.py --initialize --backup-dir <PRIVATE_BACKUP_DIR>`. Verify source rows, counts, existing movie/ledger invariants, and backup integrity. Repeat with `--apply` to validate idempotent replay.
4. Install the reviewed script under the runtime user's `~/.local/lib/media-intelligence/`, private environment file under `~/.config/media-intelligence/serialzone.env` (0600), and the sample units into `~/.config/systemd/user/`. Verify the current scheduler, account linger/boot behavior and timezone, then enable the timer only after acceptance. The weekly unit uses the existing DB, never initializes or recreates it.
5. Check one natural weekly firing and status/last successful snapshot. Failures leave the last good snapshot intact; no fallback to an empty import. Journal output is aggregate only.

## Episode history

`infer_prefix` implements the owner-approved first-N rule on an independently verified, complete ordered episode catalogue. It returns inferred membership only, unknown watched dates, and excludes any exact known state. It is deliberately not wired to canonical episode rows until series identity and episode ordering are verified against the existing catalogue/ledger. A lower count recomputes only the inferred projection; no actual watch events are generated or erased. Specials and alternative orders must be resolved rather than invented. Authenticated SerialZone access is not required for the public import; exact episode history remains optional.

No Plex, Trakt, Sonarr, Seerr, download or deletion effects.