import sqlite3
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from lifecycle_cli import watched_catalog, build_report, archived_receipts
from test_lifecycle import IDENTITY, RADARR, movie, receipt


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"test.db"
        c=sqlite3.connect(self.db)
        c.executescript("""
            CREATE TABLE movies(id INTEGER PRIMARY KEY,title TEXT,year INTEGER);
            CREATE TABLE user_movies(movie_id INTEGER,watched INTEGER);
            CREATE TABLE external_ids(movie_id INTEGER,source TEXT,external_id TEXT);
            INSERT INTO movies VALUES (1,'Fixture',2024),(2,'Untouched',2023);
            INSERT INTO user_movies VALUES (1,1),(2,0);
            INSERT INTO external_ids VALUES (1,'imdb','tt1234567'),(1,'tmdb','123');
        """)
        c.commit();c.close()

    def test_watched_catalog_identity(self):
        r=watched_catalog(self.db)
        self.assertEqual(len(r),1)
        self.assertEqual((r[0]['tmdb'],r[0]['imdb'],r[0]['watched']),(123,'tt1234567',True))

    def test_missing_ids_survive_as_needs_review(self):
        c=sqlite3.connect(self.db)
        c.execute("DELETE FROM external_ids WHERE source='imdb'");c.commit();c.close()
        r=watched_catalog(self.db)
        report=build_report(r,[RADARR],[],[],limit=2)
        self.assertEqual(report['decisions'],{'needs_review':1})

    def test_private_report_summary_and_bounded_preview(self):
        catalog=watched_catalog(self.db)
        p=build_report(catalog,[RADARR],[],[],limit=0)
        self.assertEqual(p['count'],1)
        self.assertEqual(p['items'],[])
        self.assertTrue(p['truncated'])
        self.assertEqual(p['decisions'],{'archive_eligible':1})

    def test_archive_requires_owned_receipt(self):
        c=watched_catalog(self.db)
        missing=build_report(c,[RADARR],[],[movie(1244)])
        self.assertEqual(missing['decisions'],{'needs_review':1})
        accepted=build_report(c,[RADARR],[],[movie(1244)],receipts={123:receipt()})
        self.assertEqual(accepted['decisions'],{'archived':1})

    def test_complete_private_receipt_file_is_verified(self):
        state=Path(self.tmp.name)/"state"
        state.mkdir(mode=0o700)
        payload=Path(self.tmp.name)/"Heretic.mp4"
        payload.write_bytes(b"synthetic test film data")
        receipt_data=receipt()
        receipt_data.update({"file":str(payload),
                            "sha256":hashlib.sha256(payload.read_bytes()).hexdigest()})
        file=state/"123.json"
        file.write_text(json.dumps(receipt_data))
        os.chmod(file,0o600)
        self.assertEqual(archived_receipts(str(state))[123]["stage"],"verified")
        payload.write_bytes(b"changed")
        self.assertNotIn(123,archived_receipts(str(state)))

    def test_conflicting_external_id_fails_closed(self):
        c=sqlite3.connect(self.db)
        c.executescript("""
            INSERT INTO user_movies VALUES (2,1);
            INSERT INTO external_ids VALUES (2,'imdb','tt1234567'),(2,'tmdb','456');
        """)
        c.commit();c.close()
        with self.assertRaises(ValueError):
            watched_catalog(self.db)

if __name__=='__main__':
    unittest.main()
