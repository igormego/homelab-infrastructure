#!/usr/bin/env python3
"""Import public game-detail metadata through a private CSV or authenticated GitHub Issue.

No scraping from the host, no tokens in source, no changes to personal status/logs.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
import subprocess

PRIVATE_REPO = "igormego/personal-project-brain"
SOURCE = "backloggd_public_detail"
COLUMNS = (
    "Backloggd ID", "Title", "Source URL", "Release Date", "Genres JSON",
    "Companies JSON", "Available Platforms JSON"
)
SCHEMA = """CREATE TABLE IF NOT EXISTS game_metadata (
    game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    release_date TEXT,
    genres_json TEXT,
    developers_json TEXT,
    publishers_json TEXT,
    available_platforms_json TEXT,
    source TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    credited_companies_json TEXT,
    source_url TEXT
)"""


@dataclass(frozen=True)
class Detail:
    gid: str
    title: str
    url: str
    release: str | None
    genres: str | None
    companies: str | None
    platforms: str | None


def normalize_list(value: str, label: str) -> str | None:
    if not value.strip():
        return None
    try:
        obj = json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid {label} JSON") from e
    if not isinstance(obj, list) or len(obj) > 100 or any(
        not isinstance(x, str) or not x.strip() or len(x) > 160 for x in obj
    ):
        raise ValueError(f"Invalid {label} entries")
    if len(obj) != len(set(obj)):
        raise ValueError(f"Duplicate {label} entries")
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if obj else None


def read_rows(stream) -> list[Detail]:
    reader = csv.DictReader(stream)
    if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
        raise ValueError("Metadata snapshot missing required CSV columns")
    result = []
    ids = set()
    for lineno, row in enumerate(reader, start=2):
        gid = (row["Backloggd ID"] or "").strip()
        title = (row["Title"] or "").strip()
        url = (row["Source URL"] or "").strip()
        released = (row["Release Date"] or "").strip()
        if not re.fullmatch(r"[1-9][0-9]{0,14}", gid) or not title:
            raise ValueError(f"Invalid ID/title on metadata row {lineno}")
        if not re.fullmatch(r"https://backloggd\.com/games/[a-z0-9-]+/", url):
            raise ValueError(f"Invalid source URL on row {lineno}")
        if gid in ids:
            raise ValueError(f"Duplicate Backloggd ID {gid}")
        ids.add(gid)
        if released:
            try:
                parsed = date.fromisoformat(released)
            except ValueError:
                raise ValueError(f"Invalid release date on row {lineno}") from None
            if parsed.isoformat() != released:
                raise ValueError(f"Non-ISO release date on row {lineno}")
        result.append(Detail(
            gid, title, url, released or None,
            normalize_list(row["Genres JSON"] or "", "genres"),
            normalize_list(row["Companies JSON"] or "", "companies"),
            normalize_list(row["Available Platforms JSON"] or "", "platforms"),
        ))
    if not result:
        raise ValueError("Refusing empty metadata snapshot")
    return result


def read_csv(path: Path) -> list[Detail]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return read_rows(stream)


def read_private_issue(issue_number: int) -> list[Detail]:
    """Read *only* the named private metadata issue, using existing gh authentication."""
    if not 1 <= issue_number <= 999999:
        raise ValueError("Invalid private issue number")
    try:
        proc = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--repo", PRIVATE_REPO, "--json", "comments"],
            check=True, capture_output=True, text=True, timeout=45
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Private GitHub issue unavailable (install/authenticate GitHub CLI); no data imported"
        ) from exc
    try:
        obj = json.loads(proc.stdout)
        bodies = [c["body"] for c in obj["comments"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Unexpected GitHub issue response") from exc
    chunks = []
    for body in bodies:
        for match in re.finditer(r"```csv\s*\n([\s\S]*?)```", body):
            chunks.append(match.group(1))
    if not chunks:
        raise ValueError("No CSV comments found in private metadata issue")
    records = []
    for chunk in chunks:
        records.extend(read_rows(StringIO(chunk)))
    ids = [r.gid for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate game ID across private issue batches")
    return records


def import_metadata(db_path: Path, rows: list[Detail], *, dry_run=False, expected_count=None) -> int:
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"Snapshot count {len(rows)} != expected {expected_count}; refusing partial import")
    if not rows or not db_path.is_file():
        raise ValueError("Existing SQLite catalog and nonempty metadata required")
    mode = "ro" if dry_run else "rw"
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode={mode}", uri=True) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for row in rows:
            game = db.execute("SELECT id,title FROM games WHERE backloggd_game_id=?", (row.gid,)).fetchone()
            if not game:
                raise ValueError(f"Unknown Backloggd ID {row.gid}; import cancelled")
            if game[1].casefold() != row.title.casefold():
                raise ValueError(f"Title mismatch for game {row.gid}; import cancelled")
        if dry_run:
            return len(rows)
        with db:
            db.execute(SCHEMA)
            columns = {r[1] for r in db.execute("PRAGMA table_info(game_metadata)")}
            for col in ("credited_companies_json", "source_url"):
                if col not in columns:
                    db.execute(f"ALTER TABLE game_metadata ADD COLUMN {col} TEXT")
            for row in rows:
                db.execute(
                    """INSERT INTO game_metadata
                       (game_id,release_date,genres_json,available_platforms_json,
                        credited_companies_json,source,source_url)
                       VALUES ((SELECT id FROM games WHERE backloggd_game_id=?),?,?,?,?,?,?)
                       ON CONFLICT(game_id) DO UPDATE SET
                        release_date=excluded.release_date,
                        genres_json=excluded.genres_json,
                        available_platforms_json=excluded.available_platforms_json,
                        credited_companies_json=excluded.credited_companies_json,
                        source=excluded.source,source_url=excluded.source_url,
                        updated_at=CURRENT_TIMESTAMP""",
                    (row.gid,row.release,row.genres,row.platforms,row.companies,SOURCE,row.url)
                )
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Private snapshot of public Backloggd game metadata")
    ap.add_argument("--db", required=True, type=Path)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path)
    src.add_argument("--gh-issue", type=int, help="Private Project OS metadata issue (requires gh login)")
    ap.add_argument("--expected-count", type=int, help="Reject incomplete snapshot")
    ap.add_argument("--dry-run", action="store_true", help="Validate but do not write")
    args = ap.parse_args()
    rows = read_csv(args.csv) if args.csv else read_private_issue(args.gh_issue)
    count = import_metadata(args.db, rows, dry_run=args.dry_run, expected_count=args.expected_count)
    print(f"{'Validated' if args.dry_run else 'Imported'} {count} game metadata records"
          + ("; database unchanged" if args.dry_run else ""))


if __name__ == "__main__":
    main()
