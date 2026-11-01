#!/usr/bin/env python3
"""Check one private manifest; fetch and apply full snapshots only when its version advances."""
import argparse
import json
from pathlib import Path
import sqlite3

from snapshot_sync import (
    MANIFEST_ISSUE, cursor, fenced, gh_issue, load_bundle, manifest_version, sync,
)


def poll(db_path: Path, *, fetch=gh_issue, apply=False):
    if not db_path.is_file():
        raise FileNotFoundError("Existing games.db required")
    blocks = fenced(fetch(MANIFEST_ISSUE)["body"], "json")
    if len(blocks) != 1:
        raise ValueError("Expected one versioned JSON manifest")
    manifest = json.loads(blocks[0])
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    generation = manifest_version(manifest["generation"])
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        previous = cursor(db)
    if previous:
        old = manifest_version(previous[0])
        if generation < old:
            raise ValueError("Remote generation older than applied version")
        if generation == old:
            return "unchanged"
    if not apply:
        return "pending"
    bundle = load_bundle(fetch_issue=fetch)
    if manifest_version(bundle.generation) != generation:
        raise ValueError("Manifest changed during fetch")
    return sync(db_path, bundle, apply=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print("Result:", poll(args.db, apply=args.apply))
