"""Offline regression tests: the private console intake never rewrites Backloggd status."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_db import GameRow, import_rows
from platform_inventory_import import (
    OBSERVED_ON, CREATE_TABLE_V1, import_inventory, parse_issue, read_observations,
)


def fence(csv_text: str) -> str:
    marker = chr(96) * 3
    return "Private intake\n" + marker + "csv\n" + csv_text + "\n" + marker + "\n"


SWITCH = (
    "position,title,kind,catalog_match,backloggd_id\n"
    "S1-01,Hades,game_or_app,Hades,123\n"
    "S1-02,New Game,game_or_app,,\n"
    "S1-03,New Game,demo,,\n"
    "S1-04,Nintendo Classics,subscription_app,,\n"
)
PLAYSTATION = (
    "source_image,position,title,kind,verification\n"
    "1,1,Hades,game_or_app,visual_best_effort\n"
    "1,2,Unconfirmed Title,game_or_app,needs_review\n"
    "2,1,Marathon: Server Slam,limited_playtest,visual_best_effort\n"
)
STEAM = (
    "position,title,kind,verification\n"
    "T001,Hades,game_or_app,visual_best_effort\n"
    "T002,Frostpunk 2,game_or_app,visual_best_effort\n"
    "T003,Dishonored RHCP,game_or_app,needs_review\n"
)


class PrivateInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "games.db"
        import_rows(self.db, [GameRow("Hades", game_id="123", rating=4.5,
                                     played_status="played", backlog=True)], "backloggd_test")

    def rows(self):
        return (parse_issue("Nintendo Switch", fence(SWITCH), expected_count=4) +
                parse_issue("PlayStation", fence(PLAYSTATION), expected_count=3) +
                parse_issue("Steam (PC)", fence(STEAM), expected_count=3))

    def test_parses_platforms_and_distinguishes_demo(self):
        rows = self.rows()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[2].kind, "demo")
        self.assertEqual(rows[3].kind, "subscription_app")
        self.assertEqual(rows[6].kind, "limited_playtest")
        self.assertEqual(rows[6].source_position, "P2-1")
        self.assertEqual(rows[-1].source_position, "T003")

    def test_private_issue_numbers_are_fixed_when_loading(self):
        calls = []
        def fetch(n):
            calls.append(n)
            return fence(SWITCH if n == 107 else PLAYSTATION)
        # The official loader refuses partial snapshots even when syntactically valid.
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            read_observations(fetch)
        self.assertEqual(calls, [107])

    def test_preview_no_database_mutation(self):
        with sqlite3.connect(self.db) as con:
            original = list(con.execute("SELECT * FROM user_games"))
        self.assertEqual(import_inventory(self.db, self.rows()), (10, 3, None))
        with sqlite3.connect(self.db) as con:
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='platform_library_inventory'"
            ).fetchone())
            self.assertEqual(list(con.execute("SELECT * FROM user_games")), original)

    def test_apply_backups_and_idempotent_upserts(self):
        rows = self.rows()
        with sqlite3.connect(self.db) as con:
            original = list(con.execute("SELECT * FROM user_games"))
            original_games = con.execute("SELECT count(*) FROM games").fetchone()[0]
        result = import_inventory(self.db, rows, apply=True)
        self.assertEqual(result[:2], (10, 3))
        self.assertIsNotNone(result[2])
        self.assertTrue(result[2].is_file())
        import_inventory(self.db, rows, apply=True)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM platform_library_inventory").fetchone()[0], 10)
            self.assertEqual(con.execute("SELECT count(*) FROM games").fetchone()[0], original_games)
            self.assertEqual(list(con.execute("SELECT * FROM user_games")), original)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute(
                "SELECT game_id FROM platform_library_inventory WHERE platform='Nintendo Switch' AND title='Hades'"
            ).fetchone()[0], 1)
            self.assertIsNone(con.execute(
                "SELECT game_id FROM platform_library_inventory WHERE title='Unconfirmed Title'"
            ).fetchone()[0])
            self.assertEqual(con.execute(
                "SELECT observed_on FROM platform_library_inventory WHERE title='New Game' AND kind='demo'"
            ).fetchone()[0], OBSERVED_ON)
            self.assertEqual(con.execute(
                "SELECT observed_on FROM platform_library_inventory WHERE platform='Steam (PC)' AND title='Hades'"
            ).fetchone()[0], "2026-11-10")

    def test_bad_backloggd_cross_reference_fails_before_write(self):
        data = SWITCH.replace("S1-01,Hades,game_or_app,Hades,123",
                              "S1-01,Hades,game_or_app,Wrong Title,123")
        rows = parse_issue("Nintendo Switch", fence(data), expected_count=4)
        with self.assertRaisesRegex(ValueError, "Mismatched Backloggd"):
            import_inventory(self.db, rows, apply=True)
        with sqlite3.connect(self.db) as con:
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master WHERE name='platform_library_inventory'"
            ).fetchone())

    def test_duplicate_title_kind_fails_but_demo_is_allowed(self):
        bad = SWITCH.replace("S1-03,New Game,demo,,", "S1-03,New Game,game_or_app,,")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_issue("Nintendo Switch", fence(bad), expected_count=4)

    def test_malformed_csv_and_unknown_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "columns"):
            parse_issue("PlayStation", fence("title\nA\n"), expected_count=1)
        bad = PLAYSTATION.replace("limited_playtest", "unknown_status")
        with self.assertRaisesRegex(ValueError, "kind"):
            parse_issue("PlayStation", fence(bad), expected_count=3)

    def test_incomplete_snapshot_fails(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            parse_issue("Nintendo Switch", fence(SWITCH))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            parse_issue("PlayStation", fence(PLAYSTATION))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            parse_issue("Steam (PC)", fence(STEAM))

    def test_steam_source_validation(self):
        bad_position = STEAM.replace("T003,", "T999X,")
        with self.assertRaisesRegex(ValueError, "Steam source position"):
            parse_issue("Steam (PC)", fence(bad_position), expected_count=3)
        bad_status = STEAM.replace("needs_review", "unknown")
        with self.assertRaisesRegex(ValueError, "verification status"):
            parse_issue("Steam (PC)", fence(bad_status), expected_count=3)
        bad_header = STEAM.replace("position,title,kind,verification", "position,title,kind")
        with self.assertRaisesRegex(ValueError, "columns"):
            parse_issue("Steam (PC)", fence(bad_header), expected_count=3)

    def test_legacy_two_platform_schema_upgrade_keeps_rows(self):
        with sqlite3.connect(self.db) as con:
            con.execute(CREATE_TABLE_V1)
            con.execute(
                "INSERT INTO platform_library_inventory "
                "(id, platform, title, title_key, kind, verification, source_issue, source_position, observed_on) "
                "VALUES (77, 'Nintendo Switch', 'Legacy', 'legacy', 'game_or_app', "
                "'visual_best_effort', 107, 'S1-99', '2026-11-09')"
            )
        result = import_inventory(self.db, self.rows(), apply=True)
        self.assertEqual(result[:2], (10, 3))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT id FROM platform_library_inventory WHERE title='Legacy'"
            ).fetchone()[0], 77)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM platform_library_inventory"
            ).fetchone()[0], 11)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_missing_db_does_not_create_it(self):
        missing = Path(self.temp.name) / "nonexistent.db"
        with self.assertRaises(FileNotFoundError):
            import_inventory(missing, self.rows(), apply=True)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
