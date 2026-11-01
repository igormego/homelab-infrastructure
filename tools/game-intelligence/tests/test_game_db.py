import sqlite3
import tempfile
import unittest
from pathlib import Path
from game_db import GameRow, import_rows, parse_tab, read_csv, merge_rows, sync_public


class GameDbTest(unittest.TestCase):
    def test_csv_import_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d) / "sample.csv"
            db = Path(d) / "games.db"
            csv_path.write_text("Title,Rating,Status,Backlog\nA Game,4.5,Completed,Yes\nAnother,,Wishlist,\n", encoding="utf-8")
            for _ in range(2):
                self.assertEqual(import_rows(db, read_csv(csv_path), "csv"), 2)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT count(*) FROM sync_runs").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT played_status,backlog,rating FROM user_games WHERE game_id=1").fetchone(), ("completed", 1, 4.5))

    def test_private_snapshot_csv_keeps_ids_and_overlapping_flags(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d) / "snapshot.csv"
            db = Path(d) / "games.db"
            csv_path.write_text(
                'Title,Rating,Status,Backlog,Backloggd ID,Playing,Wishlist\n'
                '"Game, One",4.5,Played,Yes,103329,Yes,No\n'
                'Another,,Wishlist,No,68353,No,Yes\n', encoding="utf-8"
            )
            rows = read_csv(csv_path)
            self.assertEqual((rows[0].game_id, rows[0].playing, rows[0].backlog, rows[0].rating),
                             ("103329", True, True, 4.5))
            self.assertEqual(import_rows(db, rows, "private_snapshot"), 2)
            with sqlite3.connect(db) as con:
                result = con.execute(
                    "SELECT g.backloggd_game_id,u.played_status,u.playing,u.backlog FROM games g JOIN user_games u ON u.game_id=g.id WHERE g.title='Game, One'"
                ).fetchone()
                self.assertEqual(result, ("103329", "played", 1, 1))

    def test_private_snapshot_csv_rejects_invalid_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.csv"
            p.write_text("Title,Rating,Status,Backlog,Backloggd ID\\nGame,4,played,no,bad-id\\n".replace("\\n", "\n"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid Backloggd"):
                read_csv(p)

    def test_public_html_merges_tabs_and_ids(self):
        html = '<div id="user_games"><div class="card mx-auto game-cover quick-access user-rating" game_id="120" data-rating="9"><a href="/games/test/"></a><div><img alt="Example &amp; Other"></div></div></div>'
        rows = merge_rows(parse_tab(html, "played") + parse_tab(html, "backlog"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].title, rows[0].game_id, rows[0].played_status, rows[0].backlog, rows[0].rating),
                         ("Example & Other", "120", "played", True, 4.5))

    def test_ambiguous_same_title_without_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            merge_rows([GameRow("Same", game_id="1"), GameRow("Same")])

    def test_same_title_different_ids_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "games.db"
            rows = [GameRow("Remake", game_id="1"), GameRow("Remake", game_id="2")]
            self.assertEqual(import_rows(db, rows, "public"), 2)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 2)

    def test_invalid_input_leaves_db_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "games.db"
            import_rows(db, [GameRow("Okay")], "csv")
            with self.assertRaises(ValueError):
                import_rows(db, [GameRow("Same", game_id="1"), GameRow("Same")], "bad")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 1)


class PublicSyncTest(unittest.TestCase):
    @staticmethod
    def html(cards):
        body = "".join(f'<div class="card game-cover" game_id="{gid}" data-rating="{rating}"><div><img alt="{title}"></div></div>'
                       for gid, title, rating in cards)
        return f'<div id="user_games">{body}</div>'

    def test_public_tab_merge_without_network(self):
        def fetch(url):
            if "/played/" in url:
                return self.html([("42", "Some Game", "8")])
            if "/backlog/" in url:
                return self.html([("42", "Some Game", "8"), ("43", "Another Game", "")])
            return self.html([])
        rows = sync_public("example", max_pages=2, interval=0, fetch_html=fetch)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0].played_status, rows[0].backlog, rows[0].rating), ("played", True, 4.0))

    def test_repeated_page_fails_closed(self):
        cards = [(str(i), f"Game {i}", "") for i in range(40)]
        with self.assertRaisesRegex(RuntimeError, "Repeated"):
            sync_public("example", max_pages=3, interval=0, fetch_html=lambda _: self.html(cards))

    def test_scrape_refuses_unexpected_response(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            sync_public("example", max_pages=2, interval=0, fetch_html=lambda _: "<html>Access denied</html>")


if __name__ == "__main__":
    unittest.main()
