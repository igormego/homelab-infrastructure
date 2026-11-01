import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from game_db import GameRow, import_rows
from journal_import import import_journal, JournalRow
from metadata_import import read_rows, import_metadata, read_private_issue


HEADER = ("Backloggd ID,Title,Source URL,Release Date,Genres JSON,"
          "Companies JSON,Available Platforms JSON\n")
SAMPLE = ('1,Game A,https://backloggd.com/games/game-a/,2024-12-03,'
          '"[""RPG"",""Strategy""]","[""Some Studio""]","[""Windows PC""]"\n')


class MetadataImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "games.db"
        import_rows(self.db_path, [GameRow("Game A", game_id="1", rating=4.5)], "private_snapshot")

    def test_import_idempotent_and_preserves_user_data(self):
        rows = read_rows(io.StringIO(HEADER + SAMPLE))
        self.assertEqual(import_metadata(self.db_path, rows, dry_run=True, expected_count=1), 1)
        with sqlite3.connect(self.db_path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='game_metadata'").fetchone())
        for _ in range(2):
            self.assertEqual(import_metadata(self.db_path, rows, expected_count=1), 1)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM game_metadata").fetchone()[0], 1)
            md = db.execute(
                "SELECT release_date,genres_json,credited_companies_json,source_url FROM game_metadata"
            ).fetchone()
            self.assertEqual(md[0], "2024-12-03")
            self.assertEqual(json.loads(md[1]), ["RPG", "Strategy"])
            self.assertEqual(json.loads(md[2]), ["Some Studio"])
            self.assertEqual(md[3], "https://backloggd.com/games/game-a/")
            self.assertEqual(db.execute("SELECT rating FROM user_games").fetchone()[0], 4.5)

    def test_existing_journal_table_is_migrated_and_preserved(self):
        import_journal(self.db_path, [JournalRow("1", "Game A", "1:session:1",
                         "2026-08-01", None, "PlayStation 5", 120, "finished")])
        rows = read_rows(io.StringIO(HEADER + SAMPLE))
        import_metadata(self.db_path, rows)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT playtime_minutes FROM playthroughs").fetchone()[0], 120)
            self.assertEqual(db.execute("SELECT credited_companies_json FROM game_metadata").fetchone()[0], '["Some Studio"]')

    def test_partial_snapshot_rejected_before_writes(self):
        rows = read_rows(io.StringIO(HEADER + SAMPLE))
        with self.assertRaisesRegex(ValueError, "refusing partial"):
            import_metadata(self.db_path, rows, expected_count=2)
        with sqlite3.connect(self.db_path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='game_metadata'").fetchone())

    def test_wrong_identity_rejected(self):
        rows = read_rows(io.StringIO(HEADER + SAMPLE.replace("Game A", "Game B")))
        with self.assertRaisesRegex(ValueError, "Title mismatch"):
            import_metadata(self.db_path, rows)

    def test_bad_date_and_invalid_json_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid release date"):
            read_rows(io.StringIO(HEADER + SAMPLE.replace("2024-12-03", "2024-99-99")))
        with self.assertRaisesRegex(ValueError, "Invalid genres"):
            read_rows(io.StringIO(HEADER + SAMPLE.replace('"[""RPG"",""Strategy""]"', '"nope"')))

    def test_private_github_issue_comment_extraction(self):
        body = "batch\n\n```csv\n" + HEADER + SAMPLE + "```\n"
        fake = type("Result", (), {"stdout": json.dumps({"comments": [{"body": body}]})})
        with patch("metadata_import.subprocess.run", return_value=fake) as run:
            rows = read_private_issue(95)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].gid, "1")
            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ["gh", "issue", "view"])

    def test_no_private_issue_comments_aborts(self):
        fake = type("Result", (), {"stdout": json.dumps({"comments": []})})
        with patch("metadata_import.subprocess.run", return_value=fake):
            with self.assertRaisesRegex(ValueError, "No CSV comments"):
                read_private_issue(95)


if __name__ == "__main__":
    unittest.main()
