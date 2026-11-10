#!/usr/bin/env python3
"""Manually import private Switch/PlayStation/Steam library observations.

Only creates or updates platform_library_inventory. Never writes games,
user_games, playthroughs, game_metadata, or the Backloggd sync cursor.
The source is three fixed, private Project OS Issues, not public repo data.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
import re
import sqlite3
import subprocess
import unicodedata
from pathlib import Path
from uuid import uuid4

PRIVATE_REPO = "igormego/personal-project-brain"
SWITCH_ISSUE = 107
PLAYSTATION_ISSUE = 108
STEAM_ISSUE = 110
OBSERVED_ON = "2026-11-09"  # Existing Switch and PlayStation snapshot date.
OBSERVED_ON_BY_PLATFORM = {"Nintendo Switch": OBSERVED_ON, "PlayStation": OBSERVED_ON, "Steam (PC)": "2026-11-10"}
COUNTS = {"Nintendo Switch": 92, "PlayStation": 188, "Steam (PC)": 67}
KINDS = {"game_or_app", "demo", "subscription_app", "limited_playtest"}
VERIFICATIONS = {"visual_best_effort", "needs_review"}
FENCE = re.compile(r"(?m)^" + chr(96) * 3 + r"csv\s*\n([\s\S]*?)^" + chr(96) * 3 + r"\s*$")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS platform_library_inventory (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK(platform IN ('Nintendo Switch', 'PlayStation', 'Steam (PC)')),
  title TEXT NOT NULL,
  title_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('game_or_app', 'demo', 'subscription_app', 'limited_playtest')),
  verification TEXT NOT NULL CHECK(verification IN ('visual_best_effort', 'needs_review')),
  source_issue INTEGER NOT NULL,
  source_position TEXT NOT NULL,
  observed_on TEXT NOT NULL,
  game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(platform, title_key, kind)
)
"""
# Previous, already-merged PR #51 schema. Upgrade it only when its exact DDL matches.
CREATE_TABLE_V1 = CREATE_TABLE.replace(", 'Steam (PC)'", "")
UPSERT = """
INSERT INTO platform_library_inventory
(platform, title, title_key, kind, verification, source_issue, source_position, observed_on, game_id)
VALUES (?,?,?,?,?,?,?,?,?)
ON CONFLICT(platform, title_key, kind) DO UPDATE SET
title=excluded.title, verification=excluded.verification,
source_issue=excluded.source_issue, source_position=excluded.source_position,
observed_on=excluded.observed_on, game_id=COALESCE(excluded.game_id, platform_library_inventory.game_id)
"""


@dataclass(frozen=True)
class Observation:
    platform: str
    title: str
    key: str
    kind: str
    verification: str
    source_issue: int
    source_position: str
    backloggd_id: str | None = None
    catalog_match: str | None = None


def title_key(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def private_issue_body(issue_number: int) -> str:
    if issue_number not in (SWITCH_ISSUE, PLAYSTATION_ISSUE, STEAM_ISSUE):
        raise ValueError("Only three approved private intake issues may be read")
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--repo", PRIVATE_REPO,
             "--json", "body", "--jq", ".body"],
            text=True, check=True, capture_output=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Private issue read failed: use an already authenticated local gh CLI") from exc
    return result.stdout


def parse_issue(platform: str, body: str, *, expected_count: int | None = None) -> list[Observation]:
    if platform not in COUNTS:
        raise ValueError("Unsupported platform")
    blocks = FENCE.findall(body)
    if len(blocks) != 1:
        raise ValueError("Expected exactly one CSV block in private intake issue")

    is_switch = platform == "Nintendo Switch"
    is_steam = platform == "Steam (PC)"
    required = ({"position", "title", "kind", "catalog_match", "backloggd_id"} if is_switch
                else {"position", "title", "kind", "verification"} if is_steam
                else {"source_image", "position", "title", "kind", "verification"})
    issue = SWITCH_ISSUE if is_switch else STEAM_ISSUE if is_steam else PLAYSTATION_ISSUE
    reader = csv.DictReader(StringIO(blocks[0]))
    if not reader.fieldnames or set(reader.fieldnames) != required:
        raise ValueError("Unexpected private inventory CSV columns")

    rows: list[Observation] = []
    seen_positions: set[str] = set()
    seen_tiles: set[tuple[str, str]] = set()
    for data in reader:
        if None in data or any(v is None for v in data.values()):
            raise ValueError("Malformed inventory CSV row")
        title = data["title"].strip()
        kind = data["kind"].strip()
        if not title or len(title) > 300 or any(ord(c) < 32 for c in title):
            raise ValueError("Invalid inventory title")
        if kind not in KINDS:
            raise ValueError("Invalid software kind")

        if is_switch:
            position = data["position"].strip()
            if not re.fullmatch(r"S[1-5]-[0-9]{2}", position):
                raise ValueError("Invalid Switch source position")
            backloggd_id = data["backloggd_id"].strip() or None
            catalog_match = data["catalog_match"].strip() or None
            if bool(backloggd_id) != bool(catalog_match):
                raise ValueError("Backloggd ID and catalog title must appear together")
            if backloggd_id and not re.fullmatch(r"[1-9][0-9]{0,14}", backloggd_id):
                raise ValueError("Invalid Backloggd ID")
            verification = "visual_best_effort"
        elif is_steam:
            position = data["position"].strip()
            if not re.fullmatch(r"T[0-9]{3}", position):
                raise ValueError("Invalid Steam source position")
            backloggd_id = None
            catalog_match = None
            verification = data["verification"].strip()
            if verification not in VERIFICATIONS:
                raise ValueError("Invalid verification status")
        else:
            image = data["source_image"].strip()
            pos = data["position"].strip()
            if not image.isascii() or not pos.isascii() or not image.isdecimal() or not pos.isdecimal():
                raise ValueError("Invalid PlayStation source position")
            if not (1 <= int(image) <= 24 and 1 <= int(pos) <= 8):
                raise ValueError("Out-of-range PlayStation source position")
            position = "P" + image + "-" + pos
            backloggd_id = None
            catalog_match = None
            verification = data["verification"].strip()
            if verification not in VERIFICATIONS:
                raise ValueError("Invalid verification status")

        key = title_key(title)
        if position in seen_positions or (key, kind) in seen_tiles:
            raise ValueError("Duplicate position or title/kind in one platform snapshot")
        seen_positions.add(position)
        seen_tiles.add((key, kind))
        rows.append(Observation(platform, title, key, kind, verification, issue,
                                position, backloggd_id, catalog_match))
    count = COUNTS[platform] if expected_count is None else expected_count
    if len(rows) != count:
        raise ValueError("Incomplete or unexpected platform inventory snapshot")
    return rows


def read_observations(fetch=private_issue_body) -> list[Observation]:
    return (parse_issue("Nintendo Switch", fetch(SWITCH_ISSUE)) +
            parse_issue("PlayStation", fetch(PLAYSTATION_ISSUE)) +
            parse_issue("Steam (PC)", fetch(STEAM_ISSUE)))


def resolve_links(con: sqlite3.Connection, rows: list[Observation]) -> list[tuple[Observation, int | None]]:
    game_rows = con.execute("SELECT id, title, backloggd_game_id FROM games").fetchall()
    ids = {str(gid): (pk, name) for pk, name, gid in game_rows if gid is not None}
    by_title: dict[str, list[int]] = {}
    for pk, name, _ in game_rows:
        by_title.setdefault(title_key(name), []).append(pk)
    linked: list[tuple[Observation, int | None]] = []
    for row in rows:
        game_id = None
        if row.backloggd_id:
            match = ids.get(row.backloggd_id)
            if match:
                if title_key(match[1]) != title_key(row.catalog_match or ""):
                    raise ValueError("Mismatched Backloggd ID and canonical game title")
                game_id = match[0]
        elif row.verification != "needs_review":
            candidates = by_title.get(row.key, [])
            if len(candidates) == 1:
                game_id = candidates[0]
        linked.append((row, game_id))
    return linked



def ensure_platform_table(con: sqlite3.Connection) -> None:
    """Create the table or safely upgrade only the exact legacy two-platform schema.

    SQLite cannot ALTER a CHECK constraint, so the reviewed v1 table is copied
    transactionally. Unrecognized schema changes or inbound FKs fail closed.
    """
    record = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='platform_library_inventory'"
    ).fetchone()
    if record is None:
        con.execute(CREATE_TABLE)
        return
    normalize = lambda value: " ".join(value.split())
    if normalize(record[0]) == normalize(CREATE_TABLE):
        return
    if normalize(record[0]) != normalize(CREATE_TABLE_V1):
        raise RuntimeError("Unexpected platform inventory table schema; manual review required")
    for (other,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if other == "platform_library_inventory":
            continue
        if any(fk[2] == "platform_library_inventory" for fk in con.execute(
            "PRAGMA foreign_key_list(" + '"' + other.replace('"', '""') + '"' + ")"
        )):
            raise RuntimeError("Existing inbound platform-library foreign keys; manual review required")

    replacement = "platform_library_inventory_v2_temp"
    if con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (replacement,)).fetchone():
        raise RuntimeError("Temporary migration name already exists")
    con.execute(CREATE_TABLE.replace(
        "CREATE TABLE IF NOT EXISTS platform_library_inventory",
        "CREATE TABLE " + replacement,
    ))
    cols = ("id, platform, title, title_key, kind, verification, source_issue, "
            "source_position, observed_on, game_id, imported_at")
    con.execute("INSERT INTO " + replacement + "(" + cols + ") SELECT " + cols + " FROM platform_library_inventory")
    old_count = con.execute("SELECT COUNT(*) FROM platform_library_inventory").fetchone()[0]
    new_count = con.execute("SELECT COUNT(*) FROM " + replacement).fetchone()[0]
    if old_count != new_count:
        raise RuntimeError("Platform library migration count mismatch")
    con.execute("DROP TABLE platform_library_inventory")
    con.execute("ALTER TABLE " + replacement + " RENAME TO platform_library_inventory")


def import_inventory(db: Path, rows: list[Observation], *, apply: bool = False) -> tuple[int, int, Path | None]:
    """Preview by default; on apply backup the existing SQLite DB before a transaction."""
    if not db.is_file():
        raise FileNotFoundError("Existing Game Intelligence games.db is required")
    if len(rows) != len(set((r.platform, r.key, r.kind) for r in rows)):
        raise ValueError("Duplicate platform software identity")

    # Read-only mode must not modify the database or create SQLite sidecar files.
    dsn = db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(dsn, uri=True) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database failed integrity check")
        linked = resolve_links(con, rows)
    matches = sum(game_id is not None for _, game_id in linked)
    if not apply:
        return len(linked), matches, None

    with sqlite3.connect(db, timeout=30) as con:
        con.execute("PRAGMA foreign_keys=ON")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database failed integrity check before apply")
        linked = resolve_links(con, rows)
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = db.with_name(db.name + ".pre-platform-" + suffix + "-" + uuid4().hex[:8] + ".db")
        # sqlite3.backup is a consistent backup of the actual live database, including WAL.
        with sqlite3.connect(backup) as target:
            con.backup(target)
        try:
            con.execute("BEGIN IMMEDIATE")
            ensure_platform_table(con)
            for row, game_id in linked:
                con.execute(UPSERT, (row.platform, row.title, row.key, row.kind,
                                    row.verification, row.source_issue, row.source_position,
                                    OBSERVED_ON_BY_PLATFORM[row.platform], game_id))
            if con.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Foreign key violation during platform inventory import")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Integrity check failed during platform inventory import")
            con.commit()
        except Exception:
            con.rollback()
            raise
    return len(linked), matches, backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Existing private Game Intelligence SQLite database")
    parser.add_argument("--apply", action="store_true", help="Back up the database and upsert observations")
    args = parser.parse_args()
    rows = read_observations()
    total, matched, backup = import_inventory(args.db, rows, apply=args.apply)
    state = "applied" if args.apply else "preview-only; database unchanged"
    print(f"{state}: {total} platform observations ({matched} linked to existing games)")
    if backup:
        print("Pre-import backup:", backup)


if __name__ == "__main__":
    main()
