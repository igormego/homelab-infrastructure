#!/usr/bin/env bash
# User-level timer entrypoint; do not echo tokens or store them in unit files.
set -euo pipefail
umask 077
APP="${HOME}/.local/share/game-intelligence"
TOKEN_FILE="${HOME}/.config/game-intelligence/github-token"
DB="/data/agents/game-intelligence/db/games.db"
[[ -s "$TOKEN_FILE" && -f "$DB" ]] || { echo "Missing private token or SQLite catalog" >&2; exit 1; }
export GH_TOKEN
GH_TOKEN="$(cat "$TOKEN_FILE")"
exec /usr/bin/flock -w 10 "${DB}.sync.lock" /usr/bin/python3 "$APP/snapshot_poller.py" --db "$DB" --apply
