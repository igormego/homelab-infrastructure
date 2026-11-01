import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_db import GameRow, import_rows
from journal_import import import_journal, read_journal


HEADER = "Backloggd ID,Title,Finished On,Platform,Playtime Minutes,State,Session Key,Started On\n"


class JournalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "games.db"
        self.csv = Path(self.temp.name) / "journal.csv"
        import_rows(self.db, [GameRow("Test Game", game_id="123")], "sample")

    def write_csv(self, row: str):
        self.csv.write_text(HEADER + row, encoding="utf-8")
        return read_journal(self.csv)

    def test_import_and_repeat_is_idempotent(self):
        rows = self.write_csv("123,Test Game,2026-08-09,PlayStation 5,7200,Finished,123:20260809:1,\n")
        self.assertEqual(import_journal(self.db, rows, dry_run=True), 1)
        for _ in range(2):
            self.assertEqual(import_journal(self.db, rows), 1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM playthroughs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT playtime_minutes,finished_on,played_platform FROM playthroughs").fetchone(),
                             (7200, "2026-08-09", "PlayStation 5"))
            self.assertIsNotNone(db.execute("SELECT sql FROM sqlite_master WHERE name='game_metadata'").fetchone())

    def test_missing_playtime_remains_sql_null(self):
        rows = self.write_csv("123,Test Game,2026-01-21,PlayStation 5,,Finished,123:20260121:1,\n")
        import_journal(self.db, rows)
        with sqlite3.connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT playtime_minutes FROM playthroughs").fetchone()[0])

    def test_dry_run_does_not_create_new_tables(self):
        rows = self.write_csv("123,Test Game,2026-08-09,PlayStation 5,42,Finished,123:20260809:1,\n")
        import_journal(self.db, rows, dry_run=True)
        with sqlite3.connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='playthroughs'").fetchone())

    def test_unknown_game_rejected_without_schema_changes(self):
        rows = self.write_csv("999,Unknown Game,2026-08-09,PlayStation 5,,Finished,999:20260809:1,\n")
        with self.assertRaisesRegex(ValueError, "Unknown Backloggd"):
            import_journal(self.db, rows)
        with sqlite3.connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='playthroughs'").fetchone())

    def test_mismatched_game_title_rejected(self):
        rows = self.write_csv("123,Other Edition,2026-08-09,PlayStation 5,,Finished,123:20260809:1,\n")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            import_journal(self.db, rows)

    def test_invalid_dates_and_duplicate_session_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "Finished before started"):
            self.write_csv("123,Test Game,2026-01-01,PC,,Finished,123:1,2026-01-02\n")
        with self.assertRaisesRegex(ValueError, "Duplicate session"):
            self.write_csv("123,Test Game,2026-08-09,PC,12,Finished,123:1,\n"
                           "123,Test Game,2026-08-10,PC,12,Finished,123:1,\n")

    def test_no_implicit_database_creation(self):
        rows = self.write_csv("123,Test Game,2026-08-09,PC,10,Finished,123:20260809:1,\n")
        with self.assertRaises(FileNotFoundError):
            import_journal(Path(self.temp.name) / "nonexistent.db", rows)


if __name__ == "__main__":
    unittest.main()
