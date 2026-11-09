"""Synthetic-only cross-source Movie Intelligence audit tests; no host/API access."""
from copy import deepcopy
import json
import unittest

from watchlist_inventory import (InventoryUncertain, classify_watchlist,
                                 diagnose_watchlist_identities)

DATE = "2026-11-09T17:00:00Z"


def sample():
    def movie(number, *, watched=False, watchlist=True):
        return {"id": number, "title": f"Film {number}", "year": 2020,
                "watched": watched, "watchlist": watchlist,
                "tmdb": number + 100, "imdb": f"tt{number:07d}"}
    catalog = [movie(i) for i in range(1, 5)]
    catalog += [{"id": 5, "title": "Unknown identity", "year": 2020,
                 "watched": False, "watchlist": True, "tmdb": None, "imdb": None},
                movie(6, watched=True, watchlist=False)]
    return {
        "catalog": catalog,
        "plex_movies": [{"rating_key": "1", "tmdb": 101, "imdb": "tt0000001",
                         "parts": [{"exists": True, "accessible": True}]}],
        "radarr_movies": [{"id": 3, "tmdb": 102, "imdb": "tt0000002",
                           "has_file": False, "monitored": True, "queued": False}],
        "seerr_requests": [{"tmdb": 103, "imdb": None, "requested": True}],
        "sources_complete": {key: True for key in ("movie_db", "plex", "radarr", "seerr")},
        "collected_at": DATE,
    }


def run(data=None):
    return classify_watchlist(**(data or sample()))


class WatchlistClassificationTests(unittest.TestCase):
    def test_distinct_categories_and_no_personal_data_in_aggregate(self):
        before = sample()
        packet, rows = run(before)
        self.assertEqual(packet["catalog"], {"total": 6, "watched": 1,
                                             "watchlist": 5, "overlap": 0})
        self.assertEqual(packet["disposition"], {
            "playable": 1, "managed_or_requested": 2,
            "reviewable_missing": 1, "unresolved": 1,
        })
        self.assertEqual(packet["plex"]["playable_movies"], 1)
        self.assertEqual([r["disposition"] for r in rows],
                         ["playable", "managed_or_requested",
                          "managed_or_requested", "reviewable_missing", "unresolved"])
        self.assertNotIn("Film ", json.dumps(packet))
        self.assertNotIn("tt000000", json.dumps(packet))
        self.assertEqual(before, sample())

    def test_watched_only_is_not_download_candidate(self):
        packet, rows = run()
        self.assertNotIn(106, [row["tmdb"] for row in rows])
        self.assertEqual(len(rows), packet["catalog"]["watchlist"])

    def test_trash_is_not_playable_or_missing(self):
        data = sample()
        data["plex_movies"][0]["parts"] = [{"exists": False, "accessible": False}]
        packet, rows = run(data)
        self.assertEqual(packet["plex"]["playable_movies"], 0)
        self.assertEqual(rows[0]["disposition"], "unresolved")

    def test_partial_plex_identity_blocks_absence_only(self):
        data = sample()
        data["plex_movies"].append({
            "rating_key": "2", "tmdb": None, "imdb": None,
            "parts": [{"exists": True, "accessible": True}],
        })
        packet, rows = run(data)
        self.assertEqual(rows[0]["disposition"], "playable")
        self.assertEqual(rows[1]["disposition"], "managed_or_requested")
        self.assertEqual(rows[3]["disposition"], "unresolved")
        self.assertEqual(packet["plex"]["playable_movies"], 2)

    def test_conflicting_identity_is_unresolved(self):
        data = sample()
        data["plex_movies"][0]["imdb"] = "tt1234567"
        _, rows = run(data)
        self.assertEqual(rows[0]["disposition"], "unresolved")

    def test_watched_watchlist_overlap_is_counted_not_discarded(self):
        data = sample()
        data["catalog"][0]["watched"] = True
        packet, rows = run(data)
        self.assertEqual(packet["catalog"]["overlap"], 1)
        self.assertEqual(packet["catalog"]["watched"], 2)
        self.assertEqual(len(rows), 5)

    def test_seerr_false_does_not_imply_requested(self):
        data = sample()
        data["seerr_requests"][0]["requested"] = False
        _, rows = run(data)
        self.assertEqual(rows[2]["disposition"], "reviewable_missing")

    def test_no_empty_sources_imply_absence_without_proven_completeness(self):
        data = sample()
        data["seerr_requests"] = []
        data["sources_complete"]["seerr"] = False
        with self.assertRaises(InventoryUncertain):
            run(data)

    def test_untrusted_shapes_and_duplicate_ids_fail_closed(self):
        mutations = [
            lambda p: p["catalog"][0].update(path="/private"),
            lambda p: p["catalog"][0].update(year=True),
            lambda p: p["catalog"][0].update(watchlist=1),
            lambda p: p["catalog"][1].update(tmdb=101),
            lambda p: p["catalog"][1].update(id=1),
            lambda p: p["plex_movies"].append(deepcopy(p["plex_movies"][0])),
            lambda p: p["plex_movies"][0]["parts"][0].update(accessible=None),
            lambda p: p["radarr_movies"][0].update(queued="yes"),
            lambda p: p["seerr_requests"][0].update(irrelevant="raw-token"),
            lambda p: p["sources_complete"].update(plex=1),
            lambda p: p.update(collected_at="yesterday"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=str(mutation)):
                data = sample()
                mutation(data)
                with self.assertRaises(InventoryUncertain):
                    run(data)


if __name__ == "__main__":
    unittest.main()


class IdentityDiagnosticsTests(unittest.TestCase):
    def diagnose(self, data=None):
        data = data or sample()
        _aggregate, private = run(data)
        return diagnose_watchlist_identities(
            catalog=data["catalog"], plex_movies=data["plex_movies"],
            radarr_movies=data["radarr_movies"],
            seerr_requests=data["seerr_requests"], private_items=private,
        )

    def test_report_aggregate_only_canonical_coverage(self):
        result = self.diagnose()
        self.assertEqual(result["diagnostic_version"], "0.1")
        self.assertEqual(result["watchlist_total"], 5)
        self.assertEqual(result["canonical_coverage"], {
            "neither": 1, "tmdb_only": 0, "imdb_only": 0, "both": 4,
        })
        self.assertEqual(result["unresolved_total"], 1)
        self.assertEqual(result["unresolved_reasons"]["no_tmdb_or_imdb"], 1)
        self.assertEqual(sum(result["unresolved_reasons"].values()), 1)
        self.assertEqual(result["source_rows_without_canonical_identity"],
                         {"plex": 0, "radarr": 0, "seerr": 0})
        self.assertNotIn("Film", str(result))
        self.assertNotIn("tt0000", str(result))

    def test_independent_tmdb_imdb_coverage_buckets(self):
        data = sample()
        data["catalog"][0]["imdb"] = None
        data["catalog"][1]["tmdb"] = None
        result = self.diagnose(data)
        self.assertEqual(result["canonical_coverage"], {
            "neither": 1, "tmdb_only": 1, "imdb_only": 1, "both": 2,
        })

    def test_global_unknown_source_guard_is_explained(self):
        data = sample()
        data["plex_movies"].append({
            "rating_key": "2", "tmdb": None, "imdb": None,
            "parts": [{"exists": True, "accessible": True}],
        })
        result = self.diagnose(data)
        self.assertEqual(result["source_rows_without_canonical_identity"]["plex"], 1)
        self.assertEqual(result["unresolved_total"], 2)
        self.assertEqual(result["unresolved_reasons"]["no_tmdb_or_imdb"], 1)
        self.assertEqual(result["unresolved_reasons"]["global_unknown_source_guard"], 1)

    def test_conflicting_ids_are_not_assumed_missing(self):
        data = sample()
        data["plex_movies"][0]["imdb"] = "tt1234567"
        result = self.diagnose(data)
        self.assertEqual(result["unresolved_total"], 2)
        self.assertEqual(result["unresolved_reasons"]["identity_conflict"], 1)

    def test_unavailable_plex_media_is_explicitly_unresolved(self):
        data = sample()
        data["plex_movies"][0]["parts"] = [{"exists": False, "accessible": False}]
        result = self.diagnose(data)
        self.assertEqual(result["unresolved_total"], 2)
        self.assertEqual(result["unresolved_reasons"]["plex_unavailable"], 1)

    def test_reject_tampered_private_decision_rows(self):
        data = sample()
        _aggregate, private = run(data)
        private[0]["imdb"] = "tt1234567"
        with self.assertRaises(InventoryUncertain):
            diagnose_watchlist_identities(
                catalog=data["catalog"], plex_movies=data["plex_movies"],
                radarr_movies=data["radarr_movies"],
                seerr_requests=data["seerr_requests"], private_items=private,
            )
