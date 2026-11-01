#!/usr/bin/env python3
"""Import a private, explicit Backloggd journal snapshot into an existing SQLite game catalog.

No network, login, historical playtime inference, or changes to game ratings.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sqlite3

SOURCE = "backloggd_public_journal"
REQUIRED = {"Backloggd ID", "Title", "Finished On", "Platform", "Playtime Minutes", "State", "Session Key"}
STATES = {"finished", "started", "in_progress", "paused", "abandoned", ""}
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS game_metadata (
        game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
        release_date TEXT,
        genres_json TEXT,
        developers_json TEXT,
        publishers_json TEXT,
        available_platforms_json TEXT,
        source TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS playthroughs (
        id INTEGER PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_log_key TEXT NOT NULL,
        started_on TEXT,
        finished_on TEXT,
        playtime_minutes INTEGER CHECK(playtime_minutes IS NULL OR playtime_minutes >= 0),
        played_platform TEXT,
        completion_state TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source, source_log_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_playthroughs_game ON playthroughs(game_id)",
)


@dataclass(frozen=True)
class JournalRow:
    backloggd_id: str
    title: str
    source_log_key: str
    finished_on: str | None
    started_on: str | None
    platform: str | None
    playtime_minutes: int | None
    state: str | None


def iso_date(raw: str | None, name: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid {name}: expected YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError(f"Invalid {name}: expected YYYY-MM-DD")
    return value


def read_journal(path: Path) -> list[JournalRow]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("Expected journal CSV columns: " + ", ".join(sorted(REQUIRED)))
        result: list[JournalRow] = []
        seen: set[str] = set()
        for line, data in enumerate(reader, start=2):
            gid = (data["Backloggd ID"] or "").strip()
            title = (data["Title"] or "").strip()
            key = (data["Session Key"] or "").strip()
            if not re.fullmatch(r"[1-9][0-9]{0,14}", gid) or not title:
                raise ValueError(f"Missing or invalid game ID/title at line {line}")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", key):
                raise ValueError(f"Invalid session key at line {line}")
            if key in seen:
                raise ValueError(f"Duplicate session key at line {line}")
            seen.add(key)
            started = iso_date(data.get("Started On"), "Started On")
            finished = iso_date(data["Finished On"], "Finished On")
            if started and finished and started > finished:
                raise ValueError(f"Finished before started at line {line}")
            raw_minutes = (data["Playtime Minutes"] or "").strip()
            if raw_minutes and not re.fullmatch(r"\d{1,8}", raw_minutes):
                raise ValueError(f"Invalid playtime at line {line}")
            minutes = int(raw_minutes) if raw_minutes else None
            state = (data["State"] or "").strip().lower()
            if state not in STATES:
                raise ValueError(f"Invalid state at line {line}")
            if state == "finished" and not finished:
                raise ValueError(f"Finished state missing date at line {line}")
            platform = (data["Platform"] or "").strip() or None
            result.append(JournalRow(gid, title, key, finished, started,
                                     platform, minutes, state or None))
    if not result:
        raise ValueError("Refusing an empty journal snapshot")
    return result


def import_journal(db_path: Path, rows: list[JournalRow], *, dry_run: bool = False) -> int:
    if not rows:
        raise ValueError("Refusing an empty journal snapshot")
    if not db_path.is_file():
        raise FileNotFoundError("Existing game catalog required; no database created")
    mode = "ro" if dry_run else "rw"
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode={mode}", uri=True) as con:
        con.execute("PRAGMA foreign_keys=ON")
        # Validate every ID and title before any schema or data changes.
        for row in rows:
            match = con.execute("SELECT id, title FROM games WHERE backloggd_game_id=?",
                                (row.backloggd_id,)).fetchone()
            if not match:
                raise ValueError(f"Unknown Backloggd game ID {row.backloggd_id}; import cancelled")
            if match[1].casefold() != row.title.casefold():
                raise ValueError(f"Game identity mismatch for Backloggd ID {row.backloggd_id}")
        if dry_run:
            return len(rows)
        with con:
            for statement in SCHEMA_STATEMENTS:
                con.execute(statement)
            for row in rows:
                con.execute(
                    """INSERT INTO playthroughs
                       (game_id,source,source_log_key,started_on,finished_on,
                        playtime_minutes,played_platform,completion_state)
                       VALUES ((SELECT id FROM games WHERE backloggd_game_id=?),?,?,?,?,?,?,?)
                       ON CONFLICT(source,source_log_key) DO UPDATE SET
                         game_id=excluded.game_id,
                         started_on=excluded.started_on,
                         finished_on=excluded.finished_on,
                         playtime_minutes=excluded.playtime_minutes,
                         played_platform=excluded.played_platform,
                         completion_state=excluded.completion_state,
                         updated_at=CURRENT_TIMESTAMP""",
                    (row.backloggd_id, SOURCE, row.source_log_key, row.started_on,
                     row.finished_on, row.playtime_minutes, row.platform, row.state)
                )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one private Backloggd journal snapshot (no network)")
    parser.add_argument("--db", required=True, type=Path, help="Existing local Game Intelligence SQLite DB")
    parser.add_argument("--journal-csv", required=True, type=Path, help="Private journal CSV path")
    parser.add_argument("--dry-run", action="store_true", help="Validate IDs without database writes")
    args = parser.parse_args()
    count = import_journal(args.db, read_journal(args.journal_csv), dry_run=args.dry_run)
    print(f"{'Validated' if args.dry_run else 'Imported'} {count} journal sessions"
          + ("; database unchanged" if args.dry_run else ""))


if __name__ == "__main__":
    main()
