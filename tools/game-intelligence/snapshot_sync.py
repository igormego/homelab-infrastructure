#!/usr/bin/env python3
"""Guarded one-shot private GitHub snapshot -> existing Game Intelligence SQLite.

GitHub Issues are private transport only. This never fetches Backloggd directly.
Default is a read-only preview; --apply requires an explicit operator action.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from game_db import read_csv as read_catalog, merge_rows, import_rows
from journal_import import read_journal, import_journal
from metadata_import import read_rows as read_metadata, import_metadata

REPO = "igormego/personal-project-brain"
MANIFEST_ISSUE = 96
CURSOR_SQL = """CREATE TABLE IF NOT EXISTS game_sync_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    generation TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""
FENCE = re.compile(r"```(json|csv)\s*\n([\s\S]*?)```")


@dataclass(frozen=True)
class Bundle:
    generation: str
    fingerprint: str
    catalog: list
    journal: list
    metadata: list


def gh_issue(issue: int) -> dict:
    """Use existing local gh credentials; do not accept arbitrary repo or token inputs."""
    if not 1 <= issue <= 999999:
        raise ValueError("Invalid private transport issue number")
    try:
        process = subprocess.run(
            ["gh", "issue", "view", str(issue), "--repo", REPO,
             "--json", "body,comments"], check=True, text=True,
            capture_output=True, timeout=45,
        )
        value = json.loads(process.stdout)
        if not isinstance(value.get("body"), str) or not isinstance(value.get("comments"), list):
            raise ValueError("Unexpected GitHub issue response")
        return value
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "Cannot read private GitHub transport: install/authenticate gh locally "
            "with access to the private repository"
        ) from exc


def fenced(text: str, kind: str) -> list[str]:
    return [body for label, body in FENCE.findall(text) if label == kind]


def parse_csv_path(text: str, parser):
    with tempfile.TemporaryDirectory(prefix="games-sync-csv-") as directory:
        path = Path(directory) / "snapshot.csv"
        path.write_text(text, encoding="utf-8")
        return parser(path)


def manifest_version(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"(20[0-9]{2}-[0-9]{2}-[0-9]{2})\.([1-9][0-9]{0,5})", value)
    if not match:
        raise ValueError("Invalid snapshot generation format")
    datetime.strptime(match.group(1), "%Y-%m-%d")
    return match.group(1), int(match.group(2))


def load_bundle(fetch_issue=gh_issue, manifest_issue: int = MANIFEST_ISSUE) -> Bundle:
    manifest_text = fenced(fetch_issue(manifest_issue)["body"], "json")
    if len(manifest_text) != 1:
        raise ValueError("Expected exactly one private JSON manifest")
    manifest = json.loads(manifest_text[0])
    needed = ("catalog_issue", "catalog_count", "journal_issue", "journal_count",
              "metadata_issue", "metadata_count")
    if manifest.get("schema_version") != 1 or any(
        not isinstance(manifest.get(x), int) or isinstance(manifest[x], bool) or
        manifest[x] < (1 if x.endswith("issue") or x == "catalog_count" else 0)
        for x in needed
    ):
        raise ValueError("Malformed private snapshot manifest")
    generation = manifest.get("generation", "")
    manifest_version(generation)

    catalog_doc = fetch_issue(manifest["catalog_issue"])
    journal_doc = fetch_issue(manifest["journal_issue"])
    metadata_doc = fetch_issue(manifest["metadata_issue"])
    catalog_text = fenced(catalog_doc["body"], "csv")
    journal_text = fenced(journal_doc["body"], "csv")
    if len(catalog_text) != 1 or len(journal_text) != 1:
        raise ValueError("Catalog/journal issue must contain one CSV snapshot")

    catalog = merge_rows(parse_csv_path(catalog_text[0], read_catalog))
    journal = parse_csv_path(journal_text[0], read_journal)
    chunks = [blob for comment in metadata_doc["comments"] for blob in
              fenced(comment.get("body", ""), "csv")]
    if not chunks:
        raise ValueError("Metadata issue missing CSV batches")
    metadata = [row for chunk in chunks for row in read_metadata(StringIO(chunk))]

    if (len(catalog), len(journal), len(metadata)) != (
        manifest["catalog_count"], manifest["journal_count"], manifest["metadata_count"]
    ):
        raise ValueError("Incomplete private snapshot, refusing synchronization")

    games = {g.game_id: g.title for g in catalog}
    if None in games or len(games) != len(catalog):
        raise ValueError("Stable game IDs required for synchronization")
    if len({m.gid for m in metadata}) != len(metadata):
        raise ValueError("Duplicate metadata ID across snapshot batches")
    for row in metadata:
        if games.get(row.gid) != row.title:
            raise ValueError(f"Metadata not consistent with catalog ({row.gid})")
    for row in journal:
        if games.get(row.backloggd_id) != row.title:
            raise ValueError(f"Journal not consistent with catalog ({row.backloggd_id})")

    fingerprint_input = json.dumps({
        "manifest": manifest,
        "catalog": catalog_text,
        "journal": journal_text,
        "metadata": sorted(chunks),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    return Bundle(generation, fingerprint, catalog, journal, metadata)


def cursor(db: sqlite3.Connection):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_sync_cursor'"
    ).fetchone():
        return None
    return db.execute(
        "SELECT generation,payload_hash FROM game_sync_cursor WHERE singleton=1"
    ).fetchone()


def sync(db_path: Path, bundle: Bundle, *, apply=False) -> str:
    if not db_path.is_file():
        raise FileNotFoundError("Existing private games.db required")
    with sqlite3.connect(db_path) as live:
        if live.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Current SQLite database is not healthy")
        current = cursor(live)
        if current:
            if current[0] == bundle.generation and current[1] != bundle.fingerprint:
                raise ValueError("Manifest generation reused with changed payload")
            if manifest_version(bundle.generation) < manifest_version(current[0]):
                raise ValueError("Refusing older snapshot generation")
            if current == (bundle.generation, bundle.fingerprint):
                return "unchanged"
        if not apply:
            return "pending"
        # Prepare a validated candidate DB first. Neither incoming content nor
        # failed partial imports can modify the running catalog.
        version = live.execute("PRAGMA data_version").fetchone()[0]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = db_path.with_name(db_path.name + ".pre-sync-" + stamp + ".bak")
        with sqlite3.connect(backup) as dest:
            live.backup(dest)
        backup.chmod(0o600)
        with tempfile.TemporaryDirectory(prefix=".game-sync-", dir=db_path.parent) as directory:
            stage = Path(directory) / "stage.db"
            with sqlite3.connect(stage) as dest:
                live.backup(dest)
            import_rows(stage, bundle.catalog, "backloggd_private_sync")
            import_journal(stage, bundle.journal)
            import_metadata(stage, bundle.metadata, expected_count=len(bundle.metadata))
            with sqlite3.connect(stage) as tested:
                tested.execute(CURSOR_SQL)
                tested.execute(
                    """INSERT INTO game_sync_cursor(singleton,generation,payload_hash)
                       VALUES (1,?,?)
                       ON CONFLICT(singleton) DO UPDATE SET
                         generation=excluded.generation,
                         payload_hash=excluded.payload_hash,
                         applied_at=CURRENT_TIMESTAMP""",
                    (bundle.generation, bundle.fingerprint)
                )
                tested.commit()
                if tested.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Staged SQLite integrity failure")
                if tested.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("Staged SQLite foreign key failure")
                if tested.execute("SELECT COUNT(*) FROM games").fetchone()[0] < len(bundle.catalog):
                    raise RuntimeError("Staged game count is too small")
                if tested.execute("SELECT COUNT(*) FROM game_metadata").fetchone()[0] < len(bundle.metadata):
                    raise RuntimeError("Staged metadata count is too small")
                if tested.execute("SELECT COUNT(*) FROM playthroughs").fetchone()[0] < len(bundle.journal):
                    raise RuntimeError("Staged session count is too small")
            if live.execute("PRAGMA data_version").fetchone()[0] != version:
                raise RuntimeError("Live database changed during sync; no promotion")
            # SQLite backup API replaces the live DB consistently, without moving
            # file paths or their permissions. Only the validated candidate is promoted.
            with sqlite3.connect(stage) as tested:
                tested.backup(live)
            if live.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Live integrity check failed; restore backup")
        return "applied (backup: " + str(backup) + ")"


def main():
    ap = argparse.ArgumentParser(description="Private Backloggd snapshot sync (default: preview only)")
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--manifest-issue", type=int, default=MANIFEST_ISSUE)
    ap.add_argument("--apply", action="store_true", help="Backup, validate stage and update private SQLite")
    args = ap.parse_args()
    bundle = load_bundle(manifest_issue=args.manifest_issue)
    print("Manifest:", bundle.generation)
    print("Snapshot counts: games=%d, journal=%d, metadata=%d" %
          (len(bundle.catalog), len(bundle.journal), len(bundle.metadata)))
    print("Result:", sync(args.db, bundle, apply=args.apply))


if __name__ == "__main__":
    main()
