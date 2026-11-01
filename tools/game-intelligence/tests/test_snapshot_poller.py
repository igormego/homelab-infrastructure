import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_db import GameRow, import_rows
from snapshot_sync import load_bundle, sync
from snapshot_poller import poll
from test_snapshot_sync import issue_store


class ManifestPollingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "games.db"
        import_rows(self.db, [GameRow("Game A", game_id="1", rating=4.5)], "catalog")

    def store(self, **kw):
        return issue_store(**kw)

    def test_unmodified_version_fetches_manifest_only(self):
        store = self.store()
        sync(self.db, load_bundle(lambda number: store[number]), apply=True)
        fetched = []
        def fetch(number):
            fetched.append(number)
            return store[number]
        self.assertEqual(poll(self.db, fetch=fetch, apply=True), "unchanged")
        self.assertEqual(fetched, [96])

    def test_preview_only_fetches_manifest(self):
        store = self.store()
        fetched = []
        def fetch(number):
            fetched.append(number)
            return store[number]
        self.assertEqual(poll(self.db, fetch=fetch), "pending")
        self.assertEqual(fetched, [96])
        with sqlite3.connect(self.db) as con:
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master WHERE name='game_sync_cursor'"
            ).fetchone())

    def test_new_generation_fetched_and_applied(self):
        store = self.store()
        sync(self.db, load_bundle(lambda number: store[number]), apply=True)
        newer = self.store(generation="2026-11-02.1")
        self.assertTrue(poll(self.db, fetch=lambda number: newer[number], apply=True).startswith("applied"))
        self.assertEqual(poll(self.db, fetch=lambda number: newer[number], apply=True), "unchanged")

    def test_invalid_manifest_rejected_before_writes(self):
        store = self.store()
        store[96]["body"] = "```json\n{}\n```"
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            poll(self.db, fetch=lambda number: store[number], apply=True)

    def test_older_version_fails_closed(self):
        newer = self.store(generation="2026-11-02.1")
        sync(self.db, load_bundle(lambda number: newer[number]), apply=True)
        old = self.store()
        with self.assertRaisesRegex(ValueError, "older"):
            poll(self.db, fetch=lambda number: old[number], apply=True)


if __name__ == "__main__":
    unittest.main()
