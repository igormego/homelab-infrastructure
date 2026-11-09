"""Synthetic, no-network host-source tests for Movie Intelligence Issue #26."""
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from watchlist_collect import (
    InventoryUncertain, collect, normalized_catalog, plex_movies,
    radarr_movies, seerr_movie_requests, main,
)

TIME = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)


class FakePlex:
    def __init__(self):
        self.scoped_sections = [
            {"key": "1", "type": "movie", "title": "Movies"},
            {"key": "4", "type": "movie", "title": "Watched"},
        ]
        self.items = [self.item("101", 101)]
        self.calls = []

    @staticmethod
    def item(key, tmdb, exists=True, accessible=True):
        return {
            "ratingKey": key, "Guid": [{"id": f"tmdb://{tmdb}"}],
            "Media": [{"Part": [{"exists": exists, "accessible": accessible}]}],
        }

    def get(self, path):
        self.calls.append(path)
        if path == "/library/sections":
            return {"MediaContainer": {"Directory": self.scoped_sections}}
        if path.startswith("/library/sections/1/all?"):
            if "X-Plex-Container-Start=0" not in path:
                raise AssertionError("unexpected pagination")
            return {"MediaContainer": {
                "Metadata": self.items, "totalSize": len(self.items),
                "offset": 0, "size": len(self.items),
            }}
        if path.startswith("/library/metadata/") and "?checkFiles=1&includeGuids=1" in path:
            key = path.split("/metadata/")[1].split("?")[0]
            item = [x for x in self.items if x["ratingKey"] == key]
            return {"MediaContainer": {"Metadata": item}}
        raise AssertionError("unapproved Plex GET route")


class FakeRadarr:
    def __init__(self):
        self.movies = [{
            "id": 7, "tmdbId": 102, "imdbId": "tt0000002",
            "hasFile": False, "monitored": False,
        }]
        self.queue = {"page": 1, "totalRecords": 0, "records": []}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == "/api/v3/movie":
            return self.movies
        if path == "/api/v3/queue?page=1&pageSize=1000&includeUnknownMovieItems=true":
            return self.queue
        raise AssertionError("unapproved Radarr GET route")


class FakeSeerr:
    def __init__(self):
        self.results = [{
            "id": 20, "type": "movie",
            "media": {"tmdbId": 103, "mediaType": "movie"},
        }]
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == "/api/v1/request?take=100&skip=0&filter=all&mediaType=movie":
            return {
                "pageInfo": {"results": len(self.results)},
                "results": self.results, "serviceErrors": {"radarr": []},
            }
        raise AssertionError("unapproved Seerr GET route")


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = str(Path(self.tempdir.name) / "synthetic.sqlite")
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT, year INTEGER);
            CREATE TABLE user_movies (movie_id INTEGER, watched INTEGER, watchlist INTEGER);
            CREATE TABLE external_ids (movie_id INTEGER, source TEXT, external_id TEXT);
        """)
        for n in range(1, 6):
            db.execute("INSERT INTO movies VALUES (?,?,?)", (n, f"Private movie {n}", 2020))
            db.execute("INSERT INTO user_movies VALUES (?,?,?)", (n, int(n == 5), int(n != 5)))
            db.execute("INSERT INTO external_ids VALUES (?,?,?)", (n, "tmdb", str(n + 100)))
            db.execute("INSERT INTO external_ids VALUES (?,?,?)", (n, "imdb", f"tt{n:07d}"))
        db.commit()
        db.close()
        self.plex, self.radarr, self.seerr = FakePlex(), FakeRadarr(), FakeSeerr()

    def run_collect(self):
        return collect(
            db_path=self.db_path, plex=self.plex, radarr=self.radarr,
            seerr=self.seerr, movies_id=1, archive_id=4, clock=TIME,
        )

    def test_full_read_only_collection_and_aggregate_private_separation(self):
        aggregate, private = self.run_collect()
        self.assertEqual(aggregate["catalog"], {
            "total": 5, "watched": 1, "watchlist": 4, "overlap": 0,
        })
        self.assertEqual(aggregate["disposition"], {
            "playable": 1, "managed_or_requested": 2,
            "reviewable_missing": 1, "unresolved": 0,
        })
        self.assertEqual(aggregate["plex"]["playable_movies"], 1)
        self.assertEqual(aggregate["source_status"], {
            "movie_db": "ok", "plex": "ok", "radarr": "ok", "seerr": "ok",
        })
        self.assertNotIn("Private movie", str(aggregate))
        self.assertEqual(len(private), 4)
        self.assertIn("Private movie", str(private))
        self.assertEqual(self.plex.calls[0], "/library/sections")
        self.assertEqual(
            self.radarr.calls,
            ["/api/v3/movie",
             "/api/v3/queue?page=1&pageSize=1000&includeUnknownMovieItems=true"],
        )
        self.assertEqual(self.seerr.calls, [
            "/api/v1/request?take=100&skip=0&filter=all&mediaType=movie",
        ])
        # The collector must never fetch placeholder library items as actual movies.
        self.assertFalse(any("/sections/4/all" in p for p in self.plex.calls))

    def test_normalized_sqlite_wal_read_and_unknown_year(self):
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE movies SET year=NULL WHERE id=4")
        db.commit()
        before = list(db.execute("SELECT title,year FROM movies ORDER BY id"))
        records = normalized_catalog(self.db_path)
        after = list(db.execute("SELECT title,year FROM movies ORDER BY id"))
        self.assertEqual(before, after)
        self.assertEqual(records[3]["year"], None)
        self.assertFalse(records[3]["watched"])
        self.assertTrue(records[3]["watchlist"])
        db.close()

    def test_missing_personal_state_fails_not_as_false(self):
        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM user_movies WHERE movie_id=2")
        db.commit()
        db.close()
        with self.assertRaises(InventoryUncertain):
            normalized_catalog(self.db_path)

    def test_sqlite_duplicate_identities_fail_closed(self):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO external_ids VALUES (4,'imdb','tt0000001')")
        db.commit()
        db.close()
        with self.assertRaises(InventoryUncertain):
            normalized_catalog(self.db_path)

    def test_plex_lost_file_remains_unresolved(self):
        self.plex.items[0]["Media"][0]["Part"][0] = {
            "exists": False, "accessible": False,
        }
        result, rows = self.run_collect()
        self.assertEqual(rows[0]["disposition"], "unresolved")
        self.assertEqual(result["plex"]["playable_movies"], 0)

    def test_plex_unknown_guids_prevent_false_missing(self):
        self.plex.items.append({
            "ratingKey": "202", "Guid": [],
            "Media": [{"Part": [{"exists": True, "accessible": True}]}],
        })
        aggregate, rows = self.run_collect()
        self.assertEqual(rows[0]["disposition"], "playable")
        self.assertEqual(rows[3]["disposition"], "unresolved")
        self.assertEqual(aggregate["plex"]["playable_movies"], 2)

    def test_wrong_plex_section_scope_fails_without_using_archive(self):
        self.plex.scoped_sections[0]["title"] = "Watched"
        with self.assertRaises(InventoryUncertain):
            self.run_collect()
        self.assertFalse(any("/sections/4/all" in p for p in self.plex.calls))

    def test_missing_plex_file_evidence_fails_closed(self):
        del self.plex.items[0]["Media"][0]["Part"][0]["accessible"]
        with self.assertRaises(InventoryUncertain):
            self.run_collect()

    def test_unknown_radarr_queue_identity_fails_closed(self):
        self.radarr.queue = {"page": 1, "totalRecords": 1, "records": [{"movieId": None}]}
        with self.assertRaises(InventoryUncertain):
            self.run_collect()

    def test_radarr_queue_metadata_not_falsely_complete(self):
        self.radarr.queue = {"page": 1, "totalRecords": 2, "records": []}
        with self.assertRaises(InventoryUncertain):
            self.run_collect()

    def test_seerr_incomplete_page_fails_closed(self):
        self.seerr.results = self.seerr.results * 2
        # A duplicate request ID makes claims about the result set unreliable.
        with self.assertRaises(InventoryUncertain):
            self.run_collect()

    def test_seerr_service_errors_fail_closed(self):
        def broken_get(_):
            return {
                "pageInfo": {"results": 0}, "results": [],
                "serviceErrors": {"radarr": ["unavailable"]},
            }
        self.seerr.get = broken_get
        with self.assertRaises(InventoryUncertain):
            self.run_collect()

    def test_seerr_deduplicates_two_requests_for_same_tmdb(self):
        self.seerr.results.append({
            "id": 21, "type": "movie",
            "media": {"tmdbId": 103, "mediaType": "movie"},
        })
        self.assertEqual(seerr_movie_requests(self.seerr), [
            {"tmdb": 103, "imdb": None, "requested": True},
        ])

    def test_transient_api_error_redacted(self):
        def throw_with_secret(_):
            raise RuntimeError("X-Api-Key=confidential /data/private")
        self.radarr.get = throw_with_secret
        with self.assertRaises(InventoryUncertain) as ctx:
            self.run_collect()
        self.assertEqual(str(ctx.exception), "watchlist_inventory_unavailable")

    def test_cli_fail_closed_without_secrets(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"MOVIE_DB_PATH": self.db_path}, clear=True), (
                contextlib.redirect_stdout(output)):
            self.assertEqual(main(), 2)
        self.assertNotIn(self.db_path, output.getvalue())
        self.assertIn('"status": "blocked"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
