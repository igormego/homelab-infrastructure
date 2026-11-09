"""Synthetic private snapshot writer tests; no actual host or media access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchlist_snapshot import SnapshotUnavailable, publish_private_snapshot, validate_aggregate
from watchlist_collect import main

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def sample(*, collected_at=None):
    return {
        "schema_version": "0.1",
        "resource_id": "movies/watchlist-overview",
        "collected_at": (collected_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "source_status": {"movie_db": "ok", "plex": "ok", "radarr": "ok", "seerr": "ok"},
        "catalog": {"total": 690, "watched": 565, "watchlist": 125, "overlap": 0},
        "disposition": {
            "playable": 0, "managed_or_requested": 0,
            "reviewable_missing": 0, "unresolved": 125,
        },
        "plex": {"playable_movies": 50},
    }


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "operator-private"
        self.root.mkdir(mode=0o700)
        self.path = self.root / "movies-watchlist-overview.json"

    def test_atomic_publish_private_file_and_replacement(self):
        before = sample()
        publish_private_snapshot(before, self.path, now=NOW)
        self.assertEqual(json.loads(self.path.read_text()), before)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        second = sample()
        second["disposition"]["unresolved"] = 124
        second["disposition"]["reviewable_missing"] = 1
        publish_private_snapshot(second, self.path, now=NOW)
        self.assertEqual(json.loads(self.path.read_text()), second)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_private_snapshot_never_changes_on_invalid_packet(self):
        publish_private_snapshot(sample(), self.path, now=NOW)
        baseline = self.path.read_bytes()
        bad = sample()
        bad["secret"] = "accidentally embedded provider token"
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(bad, self.path, now=NOW)
        self.assertEqual(baseline, self.path.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_no_automatic_directory_creation(self):
        missing = self.root / "does-not-exist" / "snapshot.json"
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), missing, now=NOW)
        self.assertFalse(missing.parent.exists())

    def test_reject_non_private_parent_and_symlink_parent(self):
        self.root.chmod(0o755)
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), self.path, now=NOW)
        self.root.chmod(0o700)
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.root)
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), alias / "snapshot.json", now=NOW)

    def test_reject_symlink_and_hardlink_target(self):
        target = self.root / "other"
        target.write_text("untouched")
        target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), self.path, now=NOW)
        self.assertEqual(target.read_text(), "untouched")
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), self.path, now=NOW)
        self.assertEqual(target.read_text(), "untouched")

    def test_existing_unsafe_mode_rejected(self):
        self.path.write_text("untouched")
        self.path.chmod(0o644)
        with self.assertRaises(SnapshotUnavailable):
            publish_private_snapshot(sample(), self.path, now=NOW)
        self.assertEqual(self.path.read_text(), "untouched")

    def test_no_arbitrary_relative_filename(self):
        for path in (Path("snapshot.json"), "/tmp/model-selected.json", 4):
            with self.subTest(path=str(path)):
                with self.assertRaises(SnapshotUnavailable):
                    publish_private_snapshot(sample(), path, now=NOW)

    def test_wire_contract_and_freshness_reject_untrusted_fields(self):
        changes = [
            lambda v: v.update(path="/secrets"),
            lambda v: v.update(complete=False),
            lambda v: v.update(resource_id="../movie"),
            lambda v: v["source_status"].update(seerr="partial"),
            lambda v: v["catalog"].update(watched=True),
            lambda v: v["catalog"].update(overlap=126),
            lambda v: v["disposition"].update(unresolved=124),
            lambda v: v["plex"].update(playable_movies=-1),
            lambda v: v.update(collected_at=(NOW - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            lambda v: v.update(collected_at=(NOW + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ]
        for change in changes:
            p = deepcopy(sample())
            change(p)
            with self.subTest(change=str(change)):
                with self.assertRaises(SnapshotUnavailable):
                    validate_aggregate(p, now=NOW)

    def test_no_temp_remnant_when_replace_fails(self):
        with patch.object(Path, "replace", side_effect=OSError("private-path-debug")):
            with self.assertRaises(SnapshotUnavailable) as err:
                publish_private_snapshot(sample(), self.path, now=NOW)
        self.assertEqual(str(err.exception), "movie_snapshot_unavailable")
        self.assertEqual(list(self.root.iterdir()), [])


class CLIContractTests(unittest.TestCase):
    def test_explicit_publication_only_uses_aggregate_and_keeps_secrets_off_stdout(self):
        env = {
            "MOVIE_DB_PATH": "/synthetic/example.db",
            "PLEX_BASE_URL": "http://127.0.0.1:32400",
            "PLEX_TOKEN": "synthetic-secret",
            "RADARR_BASE_URL": "http://127.0.0.1:7878",
            "RADARR_API_KEY": "synthetic-secret",
            "SEERR_BASE_URL": "http://127.0.0.1:5055",
            "SEERR_API_KEY": "synthetic-secret",
            "PLEX_MOVIES_LIBRARY_ID": "1",
            "PLEX_ARCHIVE_LIBRARY_ID": "2",
        }
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch("archive.ReadAPI", return_value=object()) as api,
            patch("watchlist_collect.collect", return_value=(sample(), [{"title": "private"}])) as collect,
            patch("watchlist_snapshot.publish_private_snapshot") as publish,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main(["--publish-private-snapshot"]), 0)
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args.args[0], sample())
        self.assertEqual(
            json.loads(stdout.getvalue())["status"], "snapshot_published"
        )
        self.assertNotIn("private", stdout.getvalue())
        self.assertNotIn("synthetic-secret", stdout.getvalue())

    def test_diagnostic_cannot_be_published_as_movie_snapshot(self):
        stdout = io.StringIO()
        with (
            patch("watchlist_collect.collect") as collect,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main([
                "--diagnose-identities", "--publish-private-snapshot"
            ]), 2)
        collect.assert_not_called()
        self.assertIn('"status": "blocked"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
