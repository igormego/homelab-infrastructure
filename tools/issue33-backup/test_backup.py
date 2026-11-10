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
    def test_execution_requires_explicit_flag_and_privileged_interactive_operator(self):
        import sys
        with mock.patch.object(sys, "argv", ["backup.py"]), \
             mock.patch.object(b, "run") as external:
            with self.assertRaisesRegex(b.Stop, "--execute"):
                b.main()
            external.assert_not_called()
        with mock.patch.object(sys, "argv", ["backup.py", "--execute"]), \
             mock.patch.object(b, "pwd", object()), \
             mock.patch.object(b.os, "geteuid", return_value=1000, create=True), \
             mock.patch.object(b, "verify_root_owned_script") as check:
            with self.assertRaisesRegex(b.Stop, "root in an interactive"):
                b.main()
            check.assert_not_called()

    def test_orphaned_cron_process_blocks_quiescence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            group = Path(tmp)
            (group / 'cgroup.procs').write_text('100\n200\n', encoding='ascii')
            with self.assertRaisesRegex(b.Stop, 'in-flight job'):
                b.require_empty_cron_cgroup(group, allowed={'100'}, must_exist=True)
            (group / 'cgroup.procs').write_text('100\n', encoding='ascii')
            b.require_empty_cron_cgroup(group, allowed={'100'}, must_exist=True)
            nested = group / 'job'; nested.mkdir()
            (nested / 'cgroup.procs').write_text('201\n', encoding='ascii')
            with self.assertRaisesRegex(b.Stop, 'in-flight job'):
                b.require_empty_cron_cgroup(group, allowed={'100'}, must_exist=True)

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

    def test_extra_volume_excluded_only_when_anonymous_empty_and_unreferenced(self):
        extra = "a" * 64
        volumes = sorted(b.EXPECTED_VOLUMES | {extra})
        info = [{"Name": n, "Driver": "local", "Mountpoint": "/var/lib/docker/volumes/" + n + "/_data",
                 "Labels": {"com.docker.volume.anonymous": ""} if n == extra else {}} for n in volumes]
        def run(argv, **kwargs):
            if argv[1:3] == ["volume", "ls"]: return ("\n".join(volumes)).encode()
            if argv[1:3] == ["ps", "-aq"]: return b"fixture-id"
            raise AssertionError(argv)
        class Entries:
            def __init__(self, values): self.values = iter(values)
            def __enter__(self): return self.values
            def __exit__(self, *args): return False
        with mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b.os.path, "realpath", side_effect=lambda p: p), \
             mock.patch.object(b.os, "scandir", return_value=Entries([])), \
             mock.patch.object(b, "docker_inspect", side_effect=[info, [{"Mounts": []}]]):
            b.validate_volumes()
        with mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b.os.path, "realpath", side_effect=lambda p: p), \
             mock.patch.object(b.os, "scandir", return_value=Entries([object()])), \
             mock.patch.object(b, "docker_inspect", return_value=info):
            with self.assertRaisesRegex(b.Stop, "not empty"): b.validate_volumes()
        with mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b.os.path, "realpath", side_effect=lambda p: p), \
             mock.patch.object(b.os, "scandir", return_value=Entries([])), \
             mock.patch.object(b, "docker_inspect", side_effect=[info, [{"Mounts": [{"Type": "volume", "Name": extra}]}]]):
            with self.assertRaisesRegex(b.Stop, "referenced"): b.validate_volumes()

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

    def test_writer_diagnostics_only_contains_bounded_safe_identifiers(self):
        output = "p42\nf3\nar\nf4\nau\nn/private/secret\nf5\np9\nf8\naw\n"
        with mock.patch.object(b, "writer_process_identity", return_value=" process=unknown owner=unknown"):
            diagnostics = b.writer_diagnostics(output)
            self.assertIn("pid=42 fd=4 access=u", diagnostics)
            self.assertIn("pid=42 fd=5 access=unknown", diagnostics)
            self.assertIn("pid=9 fd=8 access=w", diagnostics)
            self.assertNotIn("/private/secret", diagnostics)
            many = "".join(f"p{i}\nf3\naw\n" for i in range(20))
            self.assertEqual(len(b.writer_diagnostics(many).split("; ")), 8)

    def test_writer_process_identity_never_returns_cgroup_path(self):
        with mock.patch.object(Path, "read_text", side_effect=["worker\n", "0::/private/path/example.service\n"]):
            result = b.writer_process_identity("42")
        self.assertEqual(result, " process=worker owner=example.service")
        self.assertNotIn("/private", result)

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
    def test_recovery_refuses_same_boot_before_any_restart(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "b" * 64 for n in b.EXPECTED_CONTAINERS}, "units": []}
        with mock.patch.object(b, "current_boot_id", return_value="a" * 36), \
             mock.patch.object(b, "restore_recorded_services") as restore:
            with self.assertRaisesRegex(b.Stop, "reboot required"):
                b.recovery_preflight(record, "fixture", Path("fixture"))
            restore.assert_not_called()

    def test_delayed_stop_client_blocks_recovery(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            proc = Path(tmp) / "123"; proc.mkdir()
            (proc / "cmdline").write_bytes(b"/usr/bin/docker\0stop\0fixture\0")
            with self.assertRaisesRegex(b.Stop, "process still running"):
                b.require_no_stop_clients(Path(tmp))
            (proc / "cmdline").write_bytes(b"/usr/bin/docker\0inspect\0fixture\0")
            b.require_no_stop_clients(Path(tmp))

    def test_recovery_refuses_mounted_usb(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "b" * 64 for n in b.EXPECTED_CONTAINERS}, "units": []}
        with mock.patch.object(b, "current_boot_id", return_value="b" * 36), \
             mock.patch.object(b, "require_no_stop_clients"), \
             mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b, "run", return_value=b""), \
             mock.patch.object(b, "check_disk", side_effect=b.Stop("candidate partition is already mounted")):
            with self.assertRaisesRegex(b.Stop, "already mounted"):
                b.recovery_preflight(record, "fixture", Path("fixture"))

    def test_recovery_preflight_after_reboot_requires_settled_state(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "b" * 64 for n in b.EXPECTED_CONTAINERS}, "units": ["cron.service"]}
        def inspect(argv):
            return [{"Id": "b" * 64, "Name": "/" + argv[-1], "State": {"Status": "exited"}}]
        def command(argv):
            return b'inactive\n' if 'show' in argv else b''
        with mock.patch.object(b, "current_boot_id", return_value="b" * 36), \
             mock.patch.object(b, "require_no_stop_clients") as clients, \
             mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b, "run", side_effect=command), \
             mock.patch.object(b, "check_disk") as disk, \
             mock.patch.object(b, "docker_inspect", side_effect=inspect):
            b.recovery_preflight(record, "fixture", Path("fixture"))
        self.assertEqual(clients.call_count, 2)
        disk.assert_called_once()

    def test_operator_recovery_restarts_saved_services_without_backup(self):
        containers = {n: hashlib.sha256(n.encode()).hexdigest() for n in b.EXPECTED_CONTAINERS}
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": containers, "units": ["cron.service"]}
        running = set()
        started_units = set()
        def inspect(argv):
            name = argv[-1]
            return [{"Id": containers[name], "Name": "/" + name, "State": {"Status": "exited"}}]
        def command(argv):
            if 'list-jobs' in argv: return b''
            if 'show' in argv: return b'inactive\n'
            if argv[:2] == ['docker', 'inspect']:
                return b'true' if argv[-1] in running else b'false'
            if argv[:2] == ['docker', 'start']:
                running.add(argv[-1]); return b''
            if 'start' in argv:
                started_units.add(argv[-1]); return b''
            raise AssertionError(argv)
        metadata = mock.Mock(st_mode=0o40700, st_uid=0)
        user = mock.Mock(pw_name='fixture', pw_dir='/fixture')
        with mock.patch.object(b, 'pwd', mock.Mock(getpwuid=mock.Mock(return_value=user))), \
             mock.patch.object(b.os, 'geteuid', return_value=0, create=True), \
             mock.patch.object(b.sys.stdin, 'isatty', return_value=True), \
             mock.patch.dict(b.os.environ, {'SUDO_UID': '1000'}), \
             mock.patch.object(b.os, 'lstat', return_value=metadata), \
             mock.patch.object(b.os, 'close'), mock.patch.object(b, 'verify_root_owned_script'), \
             mock.patch.object(b, 'acquire_lock', return_value=99), \
             mock.patch.object(b, 'load_recovery_record', return_value=record), \
             mock.patch.object(b, 'current_boot_id', return_value='b' * 36), \
             mock.patch.object(b, 'require_no_stop_clients'), mock.patch.object(b, 'check_disk'), \
             mock.patch.object(b, 'user_systemctl', return_value=['userctl']), \
             mock.patch.object(b, 'docker_inspect', side_effect=inspect), \
             mock.patch.object(b, 'run', side_effect=command), \
             mock.patch.object(b, 'active', side_effect=lambda cmd, unit: unit in started_units), \
             mock.patch.object(b.signal, 'SIGHUP', 1, create=True), \
             mock.patch.object(b.signal, 'signal', return_value=None), \
             mock.patch('builtins.input', return_value='RECOVER'), \
             mock.patch('builtins.print'):
            b.operator_recovery(Path('/fixture/pending.json'))
        self.assertEqual(running, set(containers.values()))
        self.assertEqual(started_units, {'cron.service'})

    def test_reconciliation_clears_only_record_after_explicit_confirmation(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: 'b' * 64 for n in b.EXPECTED_CONTAINERS}, "units": []}
        metadata = mock.Mock(st_mode=0o40700, st_uid=0)
        user = mock.Mock(pw_name='fixture', pw_dir='/fixture')
        with mock.patch.object(b, 'pwd', mock.Mock(getpwuid=mock.Mock(return_value=user))), \
             mock.patch.object(b.os, 'geteuid', return_value=0, create=True), \
             mock.patch.object(b.sys.stdin, 'isatty', return_value=True), \
             mock.patch.dict(b.os.environ, {'SUDO_UID': '1000'}), \
             mock.patch.object(b.os, 'lstat', return_value=metadata), \
             mock.patch.object(b.os, 'close'), mock.patch.object(b.os, 'open', return_value=98), \
             mock.patch.object(b.os, 'fsync'), mock.patch.object(b.os, 'O_DIRECTORY', 0, create=True), \
             mock.patch.object(b.os, 'unlink') as unlink, mock.patch.object(b, 'verify_root_owned_script'), \
             mock.patch.object(b, 'acquire_lock', return_value=99), \
             mock.patch.object(b, 'load_recovery_record', return_value=record), \
             mock.patch.object(b, 'recovery_preflight'), mock.patch.object(b, 'restore_recorded_services'), \
             mock.patch.object(b, 'docker_inspect', return_value=[{'State': {'Running': True}}]), \
             mock.patch.object(b, 'user_systemctl', return_value=['userctl']), \
             mock.patch('builtins.input', side_effect=['RECOVER', 'RECONCILED']), mock.patch('builtins.print'):
            b.operator_recovery(Path('/fixture/pending.json'))
        unlink.assert_called_once_with(Path('/fixture/pending.json'))

    def test_catchable_signal_waits_for_stop_to_settle(self):
        handlers = {}
        events = []
        def register(sig, handler):
            previous = handlers.get(sig)
            handlers[sig] = handler
            return previous
        def operation(argv):
            events.append("stop-started")
            handlers[b.signal.SIGTERM](b.signal.SIGTERM, None)
            events.append("stop-finished")
        with mock.patch.object(b.signal, "SIGHUP", 1, create=True), \
             mock.patch.object(b.signal, "signal", side_effect=register), \
             mock.patch.object(b, "run", side_effect=operation):
            with self.assertRaisesRegex(b.Stop, "after stop settled"):
                b.stop_with_deferred_signals(["docker", "stop", "fixture"])
        self.assertEqual(events, ["stop-started", "stop-finished"])

    def test_recovery_record_rejects_unapproved_units_and_ids(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "a" * 64 for n in b.EXPECTED_CONTAINERS}, "units": [b.USER_TIMER]}
        b.validate_recovery_record(record)
        record["units"] = ["ssh.service"]
        with self.assertRaisesRegex(b.Stop, "unit set"):
            b.validate_recovery_record(record)

    def test_recorded_recovery_checks_all_identities_before_any_restart(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "a" * 64 for n in b.EXPECTED_CONTAINERS}, "units": []}
        with mock.patch.object(b, "docker_inspect", return_value=[{"Id": "b" * 64, "Name": "/radarr"}]), \
             mock.patch.object(b.Quiescence, "restore") as restart:
            with self.assertRaisesRegex(b.Stop, "identity changed"):
                b.restore_recorded_services(record, "fixture")
            restart.assert_not_called()
        record["units"] = []
        record["containers"]["radarr"] = "not-an-id"
        with self.assertRaisesRegex(b.Stop, "identity"):
            b.validate_recovery_record(record)

    def test_recorded_recovery_uses_only_saved_services(self):
        record = {"version": 2, "uid": 1000, "boot_id": "a" * 36,
                  "containers": {n: "a" * 64 for n in b.EXPECTED_CONTAINERS},
                  "units": ["cron.service", b.USER_TIMER]}
        def inspect(argv):
            return [{"Id": "a" * 64, "Name": "/" + argv[-1]}]
        with mock.patch.object(b, "docker_inspect", side_effect=inspect), \
             mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b.Quiescence, "restore", autospec=True) as restore:
            b.restore_recorded_services(record, "fixture")
        recovery = restore.call_args.args[0]
        self.assertEqual(recovery.units, [(["systemctl"], "cron.service"), (["userctl"], b.USER_TIMER)])
        self.assertTrue(recovery.entered)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "requires Linux filesystem semantics")
    def test_recovery_record_is_durable_exclusive_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record"
            containers = {n: {"Id": "a" * 64} for n in b.EXPECTED_CONTAINERS}
            b.create_recovery_record(path, os.getuid(), containers, [b.USER_TIMER])
            self.assertEqual(b.load_recovery_record(path, owner=os.getuid())["units"], [b.USER_TIMER])
            with self.assertRaises(FileExistsError):
                b.create_recovery_record(path, os.getuid(), containers, [])
            link = Path(tmp) / "link"; link.symlink_to(path)
            with self.assertRaises(OSError):
                b.load_recovery_record(link, owner=os.getuid())

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

    def test_uncertain_stop_does_not_race_with_recovery(self):
        containers, running, units, active, run = self.fake_world("radarr", fail_after_stop=True)
        with mock.patch.object(b, "active", side_effect=active), mock.patch.object(b, "run", side_effect=run), \
             mock.patch.object(b, "require_idle_cron"), mock.patch.object(b, "user_systemctl", return_value=["userctl"]), \
             mock.patch.object(b.Quiescence, "start_guard"), mock.patch.object(b.Quiescence, "finish_guard"), \
             mock.patch.object(b.signal, "SIGHUP", 1, create=True), mock.patch.object(b.signal, "signal", return_value=None):
            with self.assertRaisesRegex(b.Stop, "uncertain stop"):
                with b.Quiescence(1000, "fixture", containers):
                    self.fail("must not archive after failed stop")
        self.assertNotEqual(running, set(b.EXPECTED_CONTAINERS))

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
