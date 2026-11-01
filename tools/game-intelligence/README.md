# Game Intelligence v0.1 — local-first Backloggd import

Experimental, **source-only** personal videogame catalog. Uses Python 3.11+ standard library and SQLite. Designed alongside Movie Intelligence but independent of its production DB and live media services.

## First attempt (no export, sign-in or Codex needed)

From this directory on a machine with network access:

```bash
python3 game_db.py --db ./games.db --public-profile YOUR_BACKLOGGD_USERNAME --dry-run
python3 game_db.py --db ./games.db --public-profile YOUR_BACKLOGGD_USERNAME
```

The first command validates a public snapshot **without writing**. The second initializes and imports into a local SQLite database. Run a full dry-run before introducing a scheduled sync. Private deployments should use an explicit data location and backups, e.g. `/data/agents/game-intelligence/db/games.db`. There is no deployed service, cron, image, credential or automatic sync.

### Private GitHub snapshot relay (for blocked host access)

If the Homelab gets a Cloudflare "Making sure you're not a bot!" page, do **not** retry with scraped credentials or disable protection. A separate authorized client can read the public profile and stage a one-time CSV payload in a **private** GitHub Issue in the Project OS repository (transport only, not the database's source of truth). The payload includes the columns `Title,Rating,Status,Backlog,Backloggd ID,Playing,Wishlist`. The optional ID/flag columns retain canonical identity and overlapping categories.

For a privately staged issue, on a host with an **already authenticated** GitHub CLI:

```bash
gh issue view <PRIVATE_SNAPSHOT_ISSUE_NUMBER> --repo igormego/personal-project-brain --json body --jq '.body' |
  awk '/^```csv$/{inside=1;next} /^```$/{if(inside)exit} inside' > /tmp/backloggd-snapshot.csv
python3 game_db.py --db /tmp/games.db --csv /tmp/backloggd-snapshot.csv --dry-run
python3 game_db.py --db /path/to/private/games.db --csv /tmp/backloggd-snapshot.csv
```

Don't run the import until the staged CSV header and row count look correct. Delete the local temporary CSV when no longer needed. Private Issue access is required; never publish the extracted CSV or database in this public repository. This is a *snapshot*, not a continuous background sync.

If outbound scraping fails, optional **manual CSV fallback** for CSVs containing `Title, Rating, Status, Backlog`:

```bash
python3 game_db.py --db ./games.db --csv /path/to/backloggd.csv
```

The public HTML reader fetches **four status tabs** (played, playing, backlog and wishlist), follows `?page=N` pagination, uses Backloggd card IDs for identity, and converts its displayed 0–10 `data-rating` value to 0–5 stars. It enforces bounded responses, a request pause, and fail-closed behavior on fetching errors, duplicate pages and invalid identities. Public page shape and the current 40-card page size are undocumented and can change.

## Game metadata (v0.3): private snapshot pull

The homelab receives a Cloudflare anti-bot challenge when reading Backloggd. Do not automate retries against that challenge or publish this user's complete game list in this public repository. Public game-details metadata may be collected by a separately authorized reader and staged as **CSV batches inside one private GitHub Issue**, with provenance.

Import the already prepared title metadata snapshot (Project OS private Issue #95) after authenticating the existing GitHub CLI **locally on the host**. Never paste a GitHub token into chat, command history, or a public repo:

```bash
# Read-only local verification: no SQLite writes
python3 metadata_import.py --db /path/to/private/games.db --gh-issue 95 --expected-count 145 --dry-run
# Back up the actual DB first; next command updates ONLY metadata rows
python3 metadata_import.py --db /path/to/private/games.db --gh-issue 95 --expected-count 145
```

`--csv /path/to/metadata.csv` is an offline alternative that does not need GitHub CLI. The importer checks every canonical Backloggd ID and title against the existing games table, refuses incomplete snapshots when an expected count is specified, and performs idempotent transactions. It does not touch ratings, playing status, playthroughs or game count. `game_metadata` stores exact published date (if known), genre list, platform availability, combined **credited companies** and source URL. **Backloggd does not label developer vs publisher roles in the observed public markup**; `developers_json` and `publishers_json` stay NULL rather than guessing. Platform availability is not the user's actual played platform.

Future metadata refreshes use a separately published **versioned private snapshot** and its explicit Issue number. This is a repeatable pull/apply path, **not an unattended Backloggd synchronization**: the ChatGPT-connected page reader does not run inside the Homelab or GitHub Actions, and no automatic snapshot producer is currently deployed. Existing source-state and user-data synchronization are likewise one-shot until a secured transport/scheduler is implemented.

## Personal playthroughs (v0.2)

Historically played titles have no trustworthy hours or completion dates. **Do not assume zero playtime, infer a year, or fill missing data.** The owner started detailed Backloggd logging in 2026; earlier games retain their ratings and played status with unknown playtime/date. Even after 2026, absent values remain SQL `NULL`. An explicit playthrough is different from the public game's *community average* hours.

The optional journal importer works with the **existing**, private `games.db` and a privately transferred `journal.csv`, with the required CSV fields `Backloggd ID,Title,Finished On,Platform,Playtime Minutes,State,Session Key` (optional `Started On`). Each row is one logged session. The key is a source-specific deterministic identifier for that session, not a claim that Backloggd provides an API ID.

```bash
# On a host with the journal CSV and existing private games.db:
python3 journal_import.py --db /path/to/games.db --journal-csv /tmp/journal.csv --dry-run
# After taking a backup of the original database:
python3 journal_import.py --db /path/to/games.db --journal-csv /tmp/journal.csv
```

The first step validates identities without changing the DB. The write step is transactional and idempotent: it creates `playthroughs` and an empty `game_metadata` table if absent; records exact supplied platform, optional played minutes, optional start/finish dates, and session state. Session key uniqueness prevents duplicate imports. A wrong game ID/title or invalid date fails closed. The migration does not alter `games`, `user_games`, ratings, or the existing 145-game library. The `game_metadata` table is **schema only** until a separately validated Backloggd details importer is implemented; IGDB integration is not required for this milestone.

Only explicit logged sessions are counted in playtime-based analytics. Queries over the historic library should separately state the completeness of logged sessions, not treat untracked hours as zero. Do not commit a private journal snapshot into this public repository.

## Data model and accuracy

- `games`: local canonical ID, title, optional Backloggd game ID.
- `external_ids`: source IDs, ready for later cross-source matching.
- `user_games`: played substatus (if exposed), playing/backlog/wishlist flags, personal rating.
- `sync_runs`: source, count and import timestamp.
- `playthroughs` (optional v0.2): explicit personal sessions, platform, optional real minutes, optional dates.
- `game_metadata` (optional v0.2): table reserved for publicly available title metadata; currently not populated.

The publicly visible `played` collection **does not necessarily disclose completed/retired/shelved distinctions**; unknown substatus remains generic `played`. Ownership, platforms, playthroughs, play dates and trophies are **not imported**. No fabricated fields. Some games can belong to multiple tabs. Stable IDs distinguish games with identical titles.

Imports use an upsert; they don't delete vanished entries. A snapshot may be partial after a site change, so treat this as an initial data copy, not a guaranteed complete authoritative mirror. Backloggd remains authoritative for personal statuses and ratings. The imported SQLite file is private; do not commit any `.db` or exported personal records to this public repository.

## Offline tests

```bash
python3 -m unittest discover -s tests -v
```

**Acceptance status:** offline tests passed; public HTML was observed from a separate web client, **not** by this executable on the Homelab. Network access, anti-bot policy, complete user-library extraction, and Homelab deployment remain unverified. Do not claim that a scheduled sync is live.
