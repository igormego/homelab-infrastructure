# My Cinema Owner Request UI — source gate v0.1

This is a **source-only** companion UI for the existing Plex Watched archive.
It is not installed or authorized to execute live movie requests yet. It does
not modify Plex's Samsung client and cannot insert custom buttons into Plex.

## What it does

- Reads only the explicitly configured, isolated Plex Watched library, not all
  Radarr movies. The exact TMDb ID must exist there to display a request button.
- For a movie already managed in Radarr with no file, uses the existing
  `plan_reacquire_movie` / `reacquire_movie` guard and its durable journal.
- For a movie not yet managed, uses the existing
  `plan_movie_request` / `request_movie` Seerr guard. Its existing
  `already_managed` protection remains unchanged.
- Renders a tiny owner-only HTML screen with a **Stiahnuť znova** button.
  The confirmation is a real POST, with a fresh plan, expiry-bound HMAC and
  dedicated durable one-shot UI receipt. User-facing requests require no
  Codex task, API command or manually supplied fingerprint.
- Calls no archive deletion, Plex playback flag or watched-history API.
  `submitted` and `requested` are *not* evidence that a movie was downloaded.

## Security boundary

`my-cinema-server.mjs` always binds to `127.0.0.1` and requires explicit
environment configuration. No Docker image, unit, reverse proxy, public ingress
or background process is deployed by this PR.

Required private environment variables (never commit their values):

| Variable | Purpose |
| --- | --- |
| `MY_CINEMA_UI_USER`, `MY_CINEMA_UI_PASSWORD` | HTTP Basic credentials; password at least 16 characters |
| `MY_CINEMA_UI_SIGNING_SECRET` | Independent HMAC key, at least 32 characters |
| `MY_CINEMA_UI_JOURNAL_DIR` | Existing absolute POSIX 0700 directory for never-expiring UI receipts |
| `REACQUIRE_JOURNAL_DIR` | Existing durable private reacquisition journal, shared with any other operator |
| `MY_CINEMA_PORT` | Optional loopback port (default 8733) |
| `PLEX_BASE_URL`, `PLEX_TOKEN` | Internal Plex server, private key |
| `PLEX_MOVIES_LIBRARY_ID`, `PLEX_ARCHIVE_LIBRARY_ID` | Exact main and archive IDs |
| `RADARR_BASE_URL`, `RADARR_API_KEY` | Canonical existing Radarr endpoint |
| `SEERR_BASE_URL`, `SEERR_API_KEY` | Existing Seerr endpoint |
| `MOVIE_DB_PATH` | Existing normalized SQLite media.db; read-only |

Basic authentication on plain HTTP is suitable **only on loopback**. To expose
the UI on LAN or Meshnet, first approve a TLS proxy or authenticated tunnel
with explicit network and permission boundaries. Never expose this listener
directly, and never pass API credentials to the browser. The HTML uses no
JavaScript or third-party resources.

The UI check includes authorization, archived-membership re-read, CSRF token,
origin check, fresh plan comparison and an exclusive, fsynced local receipt
before calling either backend. A duplicate or uncertain outcome stops until
an operator reconciles that exact receipt and upstream state. There is no
automatic retry, receipt expiry or silent rollback.

### Important limitations (not live-accepted)

- The underlying Seerr `request_movie` controller protects against duplicate
  requests in one process; the UI receipt additionally blocks repeat requests
  from this UI across restarts, but cannot serialize other independent Seerr
  callers. A future system-level request broker is needed for cross-channel
  arbitration.
- The current `reacquire_movie` implementation supports one lifetime operation
  per Radarr server/TMDb under its existing journal. A reviewed rearm protocol is
  required for a later archive → active → archive → reacquire cycle.
- An archived placeholder itself remains playable as a synthetic 60-second
  message in Plex. The UI is a **companion page**, not a native Samsung button.
- No automatic archive lifecycle, historical bulk backfill, poster sync or
  download-import watcher is included here. Those require a separate acceptance
  gate before modifying files.
- The UI only displays entries with exact Plex TMDb IDs. It fails closed on
  ambiguous or incomplete archive library listings.

## Offline tests

Run from `tools/movie-intelligence`:

```sh
node --test test/test_my_cinema_ui.mjs
node --test test/*.test.mjs
```

Tests exercise authentication, escaping, origin/CSRF, fresh-plan verification,
one-shot journaling, request/reacquire lane choice and uncertainty handling.
These are offline synthetic tests; no host access or movie mutation occurs.

## Next acceptance gate

Provision private secrets and loopback journal on the Homelab; validate the
installed Node runtime and real read-only archive listing. Test one owner UI
`GET` and one read-only plan. Enable a limited, authenticated user action
only after the operator accepts its exact request scope and confirms the
network/tunnel arrangement. No actual movie search or download is authorized
by this source change.

See [Historical Watched Archive](ARCHIVE.md),
[Reacquisition](REACQUIRE.md) and
[Seerr request](SEERR.md) for existing authoritative contracts.
