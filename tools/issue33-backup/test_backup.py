import importlib.util
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("issue33_backup", Path(__file__).with_name("backup.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class ProfileTests(unittest.TestCase):
    def test_unplanned_mount_is_rejected_before_archive(self):
        b.require_accounted_root_mounts([{"target": "/", "children": [{"target": "/data"}, {"target": "/boot/efi"}]}])
        with self.assertRaisesRegex(b.Stop, "unaccounted filesystem"):
            b.require_accounted_root_mounts([{"target": "/", "children": [{"target": "/home"}]}])
        with self.assertRaisesRegex(b.Stop, "topology"):
            b.require_accounted_root_mounts([])

    def test_archive_scope_excludes_raw_docker_and_bulk_payloads(self):
        vols = {x: "/var/lib/docker/volumes/" + x + "/_data" for x in b.EXPECTED_VOLUMES}
        specs = dict(b.archive_specs(vols))
        self.assertEqual(len(specs), 9)
        self.assertIn("--exclude=./var/lib/docker", specs["root.tar.gpg"])
        self.assertIn("--exclude=./data", specs["root.tar.gpg"])
        self.assertIn("--exclude=./media", specs["docker-data.tar.gpg"])
        self.assertIn("--exclude=./downloads", specs["docker-data.tar.gpg"])
        for command in specs.values():
            self.assertIn("--one-file-system", command)
            self.assertIn("--acls", command)
            self.assertIn("--xattrs", command)

    def test_mount_comparison_does_not_ignore_write_permissions(self):
        old = [{"type": "bind", "source": "/a", "destination": "/b", "rw": True}]
        current = [{"Type": "bind", "Source": "/a", "Destination": "/b", "RW": False}]
        self.assertNotEqual(b.normalized_mounts(old), b.normalized_mounts(current))

    def test_fail_on_unexpected_running_container(self):
        with mock.patch.object(b, "run", return_value=b"one\n"):
            with self.assertRaisesRegex(b.Stop, "container count"):
                b.validate_workload({})

    def test_recovery_rejects_an_incomplete_generation(self):
        self.assertIn("networks.json.gpg", b.expected_part_names())
        self.assertEqual(len([x for x in b.expected_part_names() if x.startswith("container-")]), 13)
        self.assertEqual(len([x for x in b.expected_part_names() if x.startswith("volume-")]), 3)

    def test_lsof_writer_and_unknown_access_fail_closed(self):
        self.assertFalse(b.has_open_writer("p1\nfcwd\nf3\nar\n"))
        self.assertTrue(b.has_open_writer("p1\nf3\naw\n"))
        self.assertTrue(b.has_open_writer("p1\nf3\nau\n"))
        self.assertTrue(b.has_open_writer("p1\nf3\np2\n"))

    def test_read_error_during_writer_check_is_not_clear(self):
        result = mock.Mock(returncode=1, stdout=b"", stderr=b"warning")
        with mock.patch.object(b.os.path, "isdir", return_value=True), \
             mock.patch.object(b.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(b.Stop, "writer state"):
                b.require_no_writers(Path("fixture"), {})

    def test_metadata_scan_detects_changes_but_ignores_explicit_exclusion(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).parent.resolve()))
            root = Path(tmp) / "root"; root.mkdir()
            excluded = root / "excluded"; excluded.mkdir()
            (excluded / "payload").write_bytes(b"a")
            live = root / "config"; live.write_bytes(b"before")
            before = b.tree_fingerprint([str(root)], {str(excluded)})
            (excluded / "payload").write_bytes(b"changed")
            self.assertEqual(before, b.tree_fingerprint([str(root)], {str(excluded)}))
            live.write_bytes(b"after-more-bytes")
            self.assertNotEqual(before, b.tree_fingerprint([str(root)], {str(excluded)}))


class RecoveryTests(unittest.TestCase):
    def fake_world(self, fail_container=None, fail_after_stop=False):
        names = sorted(b.EXPECTED_CONTAINERS)
        containers = {n: {"Id": n} for n in names}
        running = set(names)
        unit_states = {u: True for u in (*b.SYSTEM_TIMERS, *b.SYSTEM_SERVICES, b.USER_TIMER)}
        unit_states.update({u: False for u in (*b.SYSTEM_ONESHOTS, b.USER_ONESHOT)})
        def active(cmd, unit):
            return unit_states[unit]
        def run(argv, **kwargs):
            if argv[0] == "docker" and argv[1] == "stop":
                name = argv[-1]
                if name == fail_container and not fail_after_stop:
                    raise b.Stop("simulated stop failure")
                running.discard(name)
                if name == fail_container:
                    raise b.Stop("simulated uncertain stop")
                return b""
            if argv[:2] == ["docker", "ps"]:
                return ("\n".join(sorted(running))).encode()
            if argv[:2] == ["docker", "inspect"]:
                return b"true" if argv[-1] in running else b"false"
            if argv[:2] == ["docker", "start"]:
                running.add(argv[-1])
                return b""
            if "stop" in argv:
                unit_states[argv[-1]] = False
                return b""
            if "start" in argv:
                unit_states[argv[-1]] = True
                return b""
            raise AssertionError(argv)
        return containers, running, unit_states, active, run

    def test_services_restore_after_failure_inside_backup(self):
        containers, running, units, active, run = self.fake_world()
        with mock.patch.object(b, "active", side_effect=active), mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b, "require_idle_cron"), mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b.Quiescence, "start_guard"), mock.patch.object(b.Quiescence, "finish_guard"), \
             mock.patch.object(b.signal, "SIGHUP", 1, create=True), mock.patch.object(b.signal, "signal", return_value=None):
            with self.assertRaisesRegex(b.Stop, "backup failed"):
                with b.Quiescence(1000, "fixture", containers):
                    self.assertFalse(running)
                    raise b.Stop("backup failed")
        self.assertEqual(running, set(b.EXPECTED_CONTAINERS))
        self.assertTrue(all(units[x] for x in (*b.SYSTEM_TIMERS, *b.SYSTEM_SERVICES, b.USER_TIMER)))

    def test_uncertain_stop_also_restarts_the_container(self):
        containers, running, units, active, run = self.fake_world("radarr", fail_after_stop=True)
        with mock.patch.object(b, "active", side_effect=active), mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b, "require_idle_cron"), mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b.Quiescence, "start_guard"), mock.patch.object(b.Quiescence, "finish_guard"), \
             mock.patch.object(b.signal, "SIGHUP", 1, create=True), mock.patch.object(b.signal, "signal", return_value=None):
            with self.assertRaisesRegex(b.Stop, "uncertain stop"):
                with b.Quiescence(1000, "fixture", containers):
                    self.fail("must not archive after failed stop")
        self.assertEqual(running, set(b.EXPECTED_CONTAINERS))
        self.assertTrue(all(units[x] for x in (*b.SYSTEM_TIMERS, *b.SYSTEM_SERVICES, b.USER_TIMER)))

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, mock.patch.object(b.os, "O_NOFOLLOW", 0, create=True):
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).parent.resolve()))
            src = Path(tmp) / "source"; src.write_bytes(b"new")
            dst = Path(tmp) / "dest"; dst.write_bytes(b"old")
            with self.assertRaises(FileExistsError):
                b.copy_exclusive(src, dst)
            self.assertEqual(dst.read_bytes(), b"old")

    def test_new_copy_must_match(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, mock.patch.object(b.os, "O_NOFOLLOW", 0, create=True):
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).parent.resolve()))
            src = Path(tmp) / "source"; src.write_bytes(b"encrypted fixture")
            dst = Path(tmp) / "dest"
            b.copy_exclusive(src, dst)
            self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_sqlite_restore_verifies_in_memory(self):
        with sqlite3.connect(":memory:") as c:
            c.execute("create table sample (v text)")
            c.execute("insert into sample values ('fixture')")
            c.commit()
            payload = c.serialize()
        proc = mock.Mock()
        proc.stdout = io.BytesIO(payload)
        proc.wait.return_value = 0
        with mock.patch.object(b, "start_gpg", return_value=proc):
            b.verify_gpg(Path("fixture"), b"not-a-secret", Path("fixture"), "sqlite")

    def test_corrupt_restored_database_is_rejected(self):
        proc = mock.Mock()
        proc.stdout = io.BytesIO(b"not a database")
        proc.wait.return_value = 0
        with mock.patch.object(b, "start_gpg", return_value=proc):
            with self.assertRaises(sqlite3.DatabaseError):
                b.verify_gpg(Path("fixture"), b"not-a-secret", Path("fixture"), "sqlite")

    def test_generation_rejects_tampered_ciphertext(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(Path(__file__).parent.resolve()))
            generation = Path(tmp) / "generation"; generation.mkdir()
            (generation / "checksums.json.gpg").write_bytes(b"fixture")
            (generation / "root.tar.gpg").write_bytes(b"modified")
            (generation / "radarr-snapshot.sqlite.gpg").write_bytes(b"sqlite")
            expected = {
                "root.tar.gpg": {"bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest()},
                "radarr-snapshot.sqlite.gpg": {"bytes": 6, "sha256": hashlib.sha256(b"sqlite").hexdigest()},
            }
            proc = mock.Mock(); proc.stdout = io.BytesIO(json.dumps(expected).encode()); proc.wait.return_value = 0
            original_temp = tempfile.TemporaryDirectory
            with mock.patch.object(b.tempfile, "TemporaryDirectory", side_effect=lambda **kw: original_temp(dir=tmp)), \
                 mock.patch.object(b, "start_gpg", return_value=proc), mock.patch.object(b.subprocess, "run"), \
                 mock.patch.object(b, "expected_part_names", return_value=set(expected)):
                with self.assertRaisesRegex(b.Stop, "hash or size"):
                    b.verify_generation(generation, b"fixture")

    def test_restored_config_checked_only_in_memory(self):
        data = b"<Config><Port>7878</Port><ApiKey>fixture</ApiKey></Config>"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as archive:
            entry = tarfile.TarInfo("./radarr/config.xml"); entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        proc = mock.Mock(); proc.stdout = io.BytesIO(buf.getvalue()); proc.wait.return_value = 0; proc.poll.return_value = 0
        with mock.patch.object(b, "start_gpg", return_value=proc):
            b.verify_radarr_config(Path("fixture"), b"fixture", Path("fixture"))

    def test_isolated_service_has_no_network_or_host_mounts_and_is_removed(self):
        cid = "a" * 64
        seen = []
        def run(argv, **kwargs):
            seen.append(argv)
            if argv[:2] == ["docker", "run"]: return (cid + "\n").encode()
            if argv[:3] == ["docker", "rm", "--force"]: return b""
            raise AssertionError(argv)
        meta = [{"Config": {"Labels": {"issue33.synthetic": "1"}},
                 "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True}, "Mounts": []}]
        with mock.patch.object(b, "run", side_effect=run), mock.patch.object(b, "docker_inspect", return_value=meta), \
             mock.patch.object(b.subprocess, "run", return_value=mock.Mock(returncode=0)):
            b.synthetic_radarr_smoke("sha256:" + "b" * 64)
        self.assertIn("--network", seen[0]); self.assertEqual(seen[0][seen[0].index("--network") + 1], "none")
        self.assertNotIn("--volume", seen[0]); self.assertNotIn("--mount", seen[0])
        self.assertEqual(seen[-1], ["docker", "rm", "--force", cid])

    def test_isolated_fixture_removed_after_uncertain_create(self):
        cid = "a" * 64
        seen = []
        def run(argv, **kwargs):
            seen.append(argv)
            if argv[:2] == ["docker", "run"]:
                raise b.Stop("simulated client failure")
            if argv[:2] == ["docker", "ps"]:
                return (cid + "\n").encode()
            if argv[:3] == ["docker", "rm", "--force"]:
                return b""
            raise AssertionError(argv)
        meta = [{"Config": {"Labels": {"issue33.synthetic": "1"}}}]
        with mock.patch.object(b, "run", side_effect=run), mock.patch.object(b, "docker_inspect", return_value=meta):
            with self.assertRaisesRegex(b.Stop, "client failure"):
                b.synthetic_radarr_smoke("sha256:" + "b" * 64)
        self.assertIn("--no-trunc", seen[1])
        self.assertEqual(seen[-1], ["docker", "rm", "--force", cid])


if __name__ == "__main__":
    unittest.main()
