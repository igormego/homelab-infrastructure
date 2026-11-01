import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_db import GameRow, import_rows
from snapshot_sync import load_bundle, sync, manifest_version


CATALOG = ("Title,Rating,Status,Backlog,Backloggd ID,Playing,Wishlist\n"
           "Game A,4.5,Played,No,1,No,No\n")
JOURNAL = ("Backloggd ID,Title,Finished On,Platform,Playtime Minutes,State,Session Key,Started On\n"
           "1,Game A,2026-08-09,PlayStation 5,120,Finished,1:20260809:1,\n")
METADATA = ('Backloggd ID,Title,Source URL,Release Date,Genres JSON,Companies JSON,Available Platforms JSON\n'
            '1,Game A,https://backloggd.com/games/game-a/,2024-01-02,'
            '"[""RPG""]","[""Studio""]","[""PlayStation 5""]"\n')


def issue_store(*, generation="2026-11-01.1", metadata=METADATA, catalog=CATALOG):
    manifest = {
        "schema_version": 1, "generation": generation,
        "catalog_issue": 92, "catalog_count": 1,
        "journal_issue": 93, "journal_count": 1,
        "metadata_issue": 95, "metadata_count": 1,
    }
    return {
        96: {"body": "```json\n" + json.dumps(manifest) + "\n```", "comments": []},
        92: {"body": "```csv\n" + catalog + "```", "comments": []},
        93: {"body": "```csv\n" + JOURNAL + "```", "comments": []},
        95: {"body": "", "comments": [{"body": "```csv\n" + metadata + "```"}]},
    }


class SnapshotSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "games.db"
        import_rows(self.db, [GameRow("Game A", game_id="1", rating=4.5)], "catalog")

    def bundle(self, **kw):
        store = issue_store(**kw)
        return load_bundle(lambda issue: store[issue])

    def test_readonly_preview_and_successful_commit(self):
        bundle = self.bundle()
        self.assertEqual((len(bundle.catalog), len(bundle.journal), len(bundle.metadata)),
                         (1, 1, 1))
        self.assertEqual(sync(self.db, bundle), "pending")
        with sqlite3.connect(self.db) as c:
            self.assertIsNone(c.execute(
                "SELECT name FROM sqlite_master WHERE name='game_sync_cursor'"
            ).fetchone())
        result = sync(self.db, bundle, apply=True)
        self.assertTrue(result.startswith("applied (backup: "))
        self.assertTrue(Path(result.removeprefix("applied (backup: ").removesuffix(")")).exists())
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM games").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT rating FROM user_games").fetchone()[0], 4.5)
            self.assertEqual(c.execute("SELECT playtime_minutes FROM playthroughs").fetchone()[0], 120)
            self.assertEqual(c.execute("SELECT release_date FROM game_metadata").fetchone()[0], "2024-01-02")
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(sync(self.db, bundle, apply=True), "unchanged")

    def test_changed_payload_same_generation_rejected(self):
        original = self.bundle()
        sync(self.db, original, apply=True)
        changed = self.bundle(metadata=METADATA.replace("RPG", "Action"))
        with self.assertRaisesRegex(ValueError, "generation reused"):
            sync(self.db, changed, apply=True)

    def test_newer_generation_is_picked_up(self):
        sync(self.db, self.bundle(), apply=True)
        newer = self.bundle(generation="2026-11-07.1")
        self.assertEqual(sync(self.db, newer), "pending")
        self.assertTrue(sync(self.db, newer, apply=True).startswith("applied"))
        self.assertEqual(sync(self.db, newer), "unchanged")

    def test_older_generation_rejected(self):
        sync(self.db, self.bundle(generation="2026-11-07.1"), apply=True)
        with self.assertRaisesRegex(ValueError, "older snapshot"):
            sync(self.db, self.bundle(), apply=True)

    def test_mismatched_catalog_metadata_rejected_before_database(self):
        store = issue_store(metadata=METADATA.replace("Game A", "Other Game"))
        with self.assertRaisesRegex(ValueError, "Metadata not consistent"):
            load_bundle(lambda issue: store[issue])
        self.assertEqual(sync(self.db, self.bundle()), "pending")

    def test_missing_chunk_rejected_before_database(self):
        store = issue_store()
        store[95]["comments"] = []
        with self.assertRaisesRegex(ValueError, "missing CSV"):
            load_bundle(lambda issue: store[issue])

    def test_malformed_manifest_rejected(self):
        store = issue_store()
        store[96]["body"] = "```json\n{}\n```"
        with self.assertRaisesRegex(ValueError, "Malformed"):
            load_bundle(lambda issue: store[issue])

    def test_generation_validates_calendar_and_numeric_revision(self):
        self.assertEqual(manifest_version("2026-11-01.2"), ("2026-11-01", 2))
        with self.assertRaises(ValueError):
            manifest_version("2026-02-31.1")


if __name__ == "__main__":
    unittest.main()
