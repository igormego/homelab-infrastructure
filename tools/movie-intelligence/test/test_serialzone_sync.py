import sqlite3
import tempfile
import unittest
from pathlib import Path
from serialzone_sync import SyncError, collect, infer_prefix, apply_snapshot, profile_url

BASE = "https://www.serialzone.cz/uzivatele/123-synthetic/"
def row(slug, extra="", classes="", rating=""):
    return f'<div class="def-list2 {classes}"><a class="dfl2-name" href="/serial/{slug}/">Synthetic {slug} <span>(2020-????)</span></a><a class="dfl2-2ndrow" href="/serial/{slug}/">Original {slug}</a><div class="dfl2-options"><span>{extra}</span></div>{rating}<div class="cleaner"></div></div>'
def score(value):
    return f'<div class="ins-score" title="1.&nbsp;2.&nbsp;2025">{value} %</div>'
def pages(count=2, rating=90):
    return {
        BASE+"hodnoceni/": 'ohodnoceno 2 seriálů'+row("a",rating=score(rating))+'<div class="pag-link pfor"><a href="strana-2/?razeni=datum">Next</a></div>'.replace('href="strana', 'href="'+BASE+'hodnoceni/strana'),
        BASE+"hodnoceni/strana-2/?razeni=datum": row("b",rating=score(70)),
        BASE+"moje/": "sleduje 1 seriálů"+row("a","od 1. 2. 2025 ❤ zamilovaný", "filtr-love filtr-neignor"),
        BASE+"rozkoukane/": "Dokoukaných 1 z 1 seriálů"+row("a",f"stav {count}/4"),
        BASE+"dokoukane/": "má 1 dokoukaných seriálů"+row("b","4 epizody"),
        BASE+"chci-videt/": "chce vidět 1 seriálů"+row("c","od 3. 4. 2025 6 epizod"),
    }

class Tests(unittest.TestCase):
    def test_public_collect(self):
        rows, counts = collect(BASE, pages().__getitem__)
        self.assertEqual(counts, {"ratings":2,"following":1,"progress":1,"completed":1,"watchlist":1})
        by = {(x[0],x[1]):x for x in rows}
        self.assertEqual(by["ratings","a"][5:7], (90,"2025-02-01"))
        self.assertEqual(by["following","a"][7:10], ("2025-02-01",1,0))
        self.assertEqual(by["progress","a"][11:13], (2,4))
        self.assertEqual(by["watchlist","c"][5], None)
        self.assertEqual(by["completed","b"][11:13], (4,4))

    def test_duplicate_partial_and_foreign_pagination(self):
        for replacement in ("", row("a",rating=score(70))):
            p = pages()
            p[BASE+"hodnoceni/strana-2/?razeni=datum"] = replacement
            with self.assertRaises(SyncError):
                collect(BASE,p.__getitem__)
        p=pages()
        p[BASE+"hodnoceni/"] = p[BASE+"hodnoceni/"].replace(BASE+"hodnoceni/strana-2/", "https://example.org/strana-2/")
        with self.assertRaises(SyncError):
            collect(BASE,p.__getitem__)

    def test_login_or_count_mismatch(self):
        p=pages()
        p[BASE+"moje/"]="Přihlášení"
        with self.assertRaises(SyncError):
            collect(BASE,p.__getitem__)

    def test_inference_reversible_and_exact_precedence(self):
        order=["s1e1","s1e2","s2e1","s2e2"]
        self.assertEqual(len(infer_prefix(3,order)),3)
        self.assertEqual(len(infer_prefix(1,order)),1)
        self.assertEqual(infer_prefix(2,order,{"s1e1":False}),[
            {"episode_id":"s1e2","provenance":"inferred_sequential","watched_at":None}])
        with self.assertRaises(SyncError):
            infer_prefix(5,order)
        with self.assertRaises(SyncError):
            infer_prefix(2,["duplicate","duplicate"])

    def test_transaction_replay_and_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"media.db"
            with sqlite3.connect(db) as conn:
                conn.executescript("""CREATE TABLE movies(id INTEGER,title TEXT,original_title TEXT,year INTEGER);
                CREATE TABLE user_movies(movie_id INTEGER,watched INTEGER,watchlist INTEGER);
                CREATE TABLE external_ids(movie_id INTEGER,source TEXT,external_id TEXT);
                CREATE TABLE watch_events(id INTEGER);
                INSERT INTO movies VALUES(1,'Unrelated',NULL,2000);
                INSERT INTO watch_events VALUES(123);""")
            rows,counts=collect(BASE,pages().__getitem__)
            with self.assertRaises(SyncError):
                apply_snapshot(db,rows,counts,"2026-01-01")
            self.assertTrue(apply_snapshot(db,rows,counts,"2026-01-01",True,tmp))
            self.assertFalse(apply_snapshot(db,rows,counts,"2026-01-02"))
            corrected,new_counts=collect(BASE,pages(1,80).__getitem__)
            self.assertTrue(apply_snapshot(db,corrected,new_counts,"2026-01-03"))
            with self.assertRaises(SyncError):
                apply_snapshot(db,rows,counts,"2026-01-02")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT id FROM movies").fetchall(),[(1,)])
                self.assertEqual(conn.execute("SELECT * FROM watch_events").fetchall(),[(123,)])
                self.assertEqual(conn.execute("SELECT episodes_watched FROM serialzone_series_observations WHERE section='progress'").fetchone(),(1,))
                self.assertEqual(conn.execute("SELECT rating_percent FROM serialzone_series_observations WHERE section='ratings' AND slug='a'").fetchone(),(80,))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM serialzone_series_observations").fetchone(),(6,))
            self.assertEqual(len(list(Path(tmp).glob("media-before-serialzone-*.db"))),1)

    def test_profile_allowlist(self):
        with self.assertRaises(SyncError):
            profile_url("https://example.org/uzivatele/123-user/")

if __name__=="__main__":
    unittest.main()
