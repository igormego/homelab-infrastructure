# Game Intelligence v0.1 — local-first Backloggd import

Experimental, **source-only** personal videogame catalog. Uses Python 3.11+ standard library and SQLite. Designed alongside Movie Intelligence but independent of its production DB and live media services.

## First attempt (no export, sign-in or Codex needed)

From this directory on a machine with network access:

\`\`\`bash
python3 game_db.py --db ./games.db --public-profile YOUR_BACKLOGGD_USERNAME --dry-run
python3 game_db.py --db ./games.db --public-profile YOUR_BACKLOGGD_USERNAME
\`\`\`

The first command validates a public snapshot **without writing**. The second initializes and imports into a local SQLite database. Run a full dry-run before introducing a scheduled sync. Private deployments should use an explicit data location and backups, e.g. \`/data/agents/game-intelligence/db/games.db\`. There is no deployed service, cron, image, credential or automatic sync.

If outbound scraping fails, optional **manual CSV fallback** for CSVs containing \`Title, Rating, Status, Backlog\`:

\`\`\`bash
python3 game_db.py --db ./games.db --csv /path/to/backloggd.csv
\`\`\`

The public HTML reader fetches **four status tabs** (played, playing, backlog and wishlist), follows \`?page=N\` pagination, uses Backloggd card IDs for identity, and converts its displayed 0–10 \`data-rating\` value to 0–5 stars. It enforces bounded responses, a request pause, and fail-closed behavior on fetching errors, duplicate pages and invalid identities. Public page shape and the current 40-card page size are undocumented and can change.

## Data model and accuracy

- \`games\`: local canonical ID, title, optional Backloggd game ID.
- \`external_ids\`: source IDs, ready for later cross-source matching.
- \`user_games\`: played substatus (if exposed), playing/backlog/wishlist flags, personal rating.
- \`sync_runs\`: source, count and import timestamp.

The publicly visible \`played\` collection **does not necessarily disclose completed/retired/shelved distinctions**; unknown substatus remains generic \`played\`. Ownership, platforms, playthroughs, play dates and trophies are **not imported**. No fabricated fields. Some games can belong to multiple tabs. Stable IDs distinguish games with identical titles.

Imports use an upsert; they don't delete vanished entries. A snapshot may be partial after a site change, so treat this as an initial data copy, not a guaranteed complete authoritative mirror. Backloggd remains authoritative for personal statuses and ratings. The imported SQLite file is private; do not commit any \`.db\` or exported personal records to this public repository.

## Offline tests

\`\`\`bash
python3 -m unittest discover -s tests -v
\`\`\`

**Acceptance status:** offline tests passed; public HTML was observed from a separate web client, **not** by this executable on the Homelab. Network access, anti-bot policy, complete user-library extraction, and Homelab deployment remain unverified. Do not claim that a scheduled sync is live.
