#!/usr/bin/env python3
"""Issue 33: fail-closed, single-operator non-media backup transaction.

The program does not run unless invoked as root with --execute. It expects the
owner's previously inventoried private profile and local disk fingerprints.
It never invokes a shell or writes plaintext credentials to staging. A completed
run verifies encrypted parts and an in-memory Radarr database restore, but does
not claim a full service or whole-host disaster-recovery rehearsal.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
try:
    import pwd
except ImportError:  # Offline tests can run on Windows; execution requires Linux.
    pwd = None
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED_CONTAINERS = frozenset({
    "personal-ai-brain-control-control-1", "personal-ai-brain-core-1",
    "trading-intelligence-platform-api-1", "seerr", "bazarr", "sonarr",
    "radarr", "netdata", "tautulli", "qbittorrent", "sabnzbd", "prowlarr", "plex",
})
EXPECTED_VOLUMES = frozenset({"netdata_netdatacache", "netdata_netdataconfig", "netdata_netdatalib"})
SYSTEM_TIMERS = ("movie-intelligence-csfd-sync.timer", "pab-host-audit-refresh.timer")
SYSTEM_ONESHOTS = ("movie-intelligence-csfd-sync.service", "pab-host-audit-refresh.service")
SYSTEM_SERVICES = ("cron.service", "personal-ai-brain-builder.service", "personal-ai-brain-inference-broker.service")
USER_TIMER = "game-intelligence-sync.timer"
USER_ONESHOT = "game-intelligence-sync.service"
FIVE_GIB = 5 * 1024**3
MIN_SOURCE_RESERVE = 50 * 1024**3


class Stop(Exception):
    """A safe stop; no confidential subprocess output is included."""


def run(argv, *, data=None, ok=(0,), env=None):
    p = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    if p.returncode not in ok:
        raise Stop(f"{Path(argv[0]).name} failed (exit {p.returncode})")
    return p.stdout


def identity_hash(text):
    # Match the previously stored sha256sum of the complete lsblk output.
    return hashlib.sha256(text).hexdigest()


def read_user_file(path, uid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != uid or stat.S_IMODE(s.st_mode) != 0o600:
            raise Stop("private profile/fingerprint ownership or mode is unsafe")
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)


def select_profile(home, uid):
    parent = home / ".local" / "state"
    options = list(parent.glob("issue33-source-manifest-*.json"))
    if len(options) != 1:
        raise Stop("expected exactly one private Issue 33 manifest")
    raw = read_user_file(options[0], uid)
    profile = json.loads(raw)
    if {c.get("service") for c in profile.get("containers", [])} != EXPECTED_CONTAINERS:
        raise Stop("private profile does not name the approved 13 containers")
    if {v.get("name") for v in profile.get("volumes", [])} != EXPECTED_VOLUMES:
        raise Stop("private profile does not name the approved three volumes")
    if len(profile["containers"]) != 13 or len(profile["volumes"]) != 3:
        raise Stop("duplicate profile entries")
    return profile, raw


def check_disk(home, uid):
    base = home / ".cache" / "issue33-inspection"
    serial = read_user_file(base / "expected-disk.sha256", uid).decode().strip()
    volume = read_user_file(base / "expected-volume.sha256", uid).decode().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", serial) or not re.fullmatch(r"[0-9a-f]{64}", volume):
        raise Stop("disk fingerprint invalid")
    def ls(dev, field):
        return run(["lsblk", "-dn", "-o", field, dev])
    devices = json.loads(run(["lsblk", "-d", "-J", "-p", "-o", "NAME,TYPE,TRAN,SIZE", "-b"]))["blockdevices"]
    candidates = [x for x in devices if x.get("type") == "disk" and x.get("tran") == "usb"
                  and 1_800_000_000_000 <= int(x.get("size") or 0) <= 2_200_000_000_000
                  and identity_hash(ls(x["name"], "SERIAL")) == serial]
    if len(candidates) != 1:
        raise Stop("expected exactly one USB disk matching the private fingerprint")
    disk = candidates[0]["name"]
    children = json.loads(run(["lsblk", "-J", "-p", "-o", "NAME,TYPE,FSTYPE", disk]))["blockdevices"]
    parts = [x for x in children[0].get("children", []) if x.get("type") == "part"
             and x.get("fstype") == "ntfs" and identity_hash(ls(x["name"], "UUID")) == volume]
    if len(parts) != 1:
        raise Stop("expected exactly one NTFS partition matching the private fingerprint")
    partition = parts[0]["name"]
    mounted = run(["lsblk", "-nr", "-o", "MOUNTPOINTS", partition])
    if mounted.strip():
        raise Stop("candidate partition is already mounted")
    for target in ("/", "/data"):
        source = run(["findmnt", "-n", "-o", "SOURCE", "--target", target]).decode().strip()
        parents = run(["lsblk", "-s", "-nr", "-o", "KNAME", source]).decode().splitlines()
        if Path(disk).name in parents:
            raise Stop("candidate shares a device with system or production data")
    run(["smartctl", "-H", "-d", "scsi", disk])
    return disk, partition


def check_mounted_disk(home, uid, mount_dir, disk, partition):
    base = home / ".cache" / "issue33-inspection"
    serial = read_user_file(base / "expected-disk.sha256", uid).decode().strip()
    volume = read_user_file(base / "expected-volume.sha256", uid).decode().strip()
    if identity_hash(run(["lsblk", "-dn", "-o", "SERIAL", disk])) != serial:
        raise Stop("USB disk identity changed during copy")
    if identity_hash(run(["lsblk", "-dn", "-o", "UUID", partition])) != volume:
        raise Stop("USB volume identity changed during copy")
    source = run(["findmnt", "-n", "-o", "SOURCE", "--target", str(mount_dir)]).decode().strip()
    target = run(["findmnt", "-n", "-o", "TARGET", "--target", str(mount_dir)]).decode().strip()
    options = set(run(["findmnt", "-n", "-o", "OPTIONS", "--target", str(mount_dir)]).decode().strip().split(","))
    nested = run(["findmnt", "-R", "-n", "-o", "TARGET", "--target", str(mount_dir)]).decode().splitlines()
    if source != partition or target != str(mount_dir) or nested != [str(mount_dir)]:
        raise Stop("USB mount source or nested mounts changed")
    if not {"rw", "nodev", "nosuid", "noexec"}.issubset(options):
        raise Stop("USB mount restrictions changed")


def docker_inspect(args):
    return json.loads(run(["docker", *args]))


def normalized_mounts(mounts):
    return sorted((m.get("type", m.get("Type")), m.get("source", m.get("Source")),
                   m.get("destination", m.get("Destination")), bool(m.get("rw", m.get("RW")))) for m in mounts)


def validate_workload(profile):
    ids = run(["docker", "ps", "-q"]).decode().split()
    if len(ids) != 13:
        raise Stop("active Docker container count differs from profile")
    actual = docker_inspect(["inspect", *ids])
    byname = {c["Name"].lstrip("/"): c for c in actual}
    if set(byname) != EXPECTED_CONTAINERS:
        raise Stop("active Docker container names differ from profile")
    for expected in profile["containers"]:
        c = byname[expected["service"]]
        if c["Image"] != expected["image_id"] or normalized_mounts(c["Mounts"]) != normalized_mounts(expected["mounts"]):
            raise Stop("container image or mounts changed since private inventory")
    validate_volumes()
    return byname


def validate_volumes():
    """Exclude at most one empty, unreferenced anonymous volume; never mutate it."""
    volumes = set(run(["docker", "volume", "ls", "-q"]).decode().split())
    extra = volumes - EXPECTED_VOLUMES
    if not EXPECTED_VOLUMES.issubset(volumes) or len(extra) > 1:
        raise Stop("Docker volumes differ from private inventory")
    info = docker_inspect(["volume", "inspect", *sorted(volumes)])
    if len(info) != len(volumes) or {v.get("Name") for v in info} != volumes:
        raise Stop("Docker volume inventory is incomplete")
    for v in info:
        expected = f"/var/lib/docker/volumes/{v['Name']}/_data"
        if v.get("Driver") != "local" or v.get("Mountpoint") != expected or os.path.realpath(expected) != expected:
            raise Stop("unexpected Docker volume backing path")
        if v["Name"] in extra:
            labels = v.get("Labels")
            if (not re.fullmatch(r"[0-9a-f]{64}", v["Name"])
                    or not isinstance(labels, dict) or "com.docker.volume.anonymous" not in labels):
                raise Stop("additional volume is not the reviewed anonymous volume type")
            try:
                with os.scandir(expected) as entries:
                    if next(entries, None) is not None:
                        raise Stop("additional anonymous volume is not empty")
            except OSError:
                raise Stop("cannot verify additional volume is empty") from None
    if extra:
        ids = run(["docker", "ps", "-aq", "--no-trunc"]).decode().split()
        containers = docker_inspect(["inspect", *ids]) if ids else []
        if len(containers) != len(ids):
            raise Stop("cannot verify additional volume references")
        for c in containers:
            mounts = c.get("Mounts")
            if not isinstance(mounts, list):
                raise Stop("cannot verify additional volume references")
            if any(m.get("Type") == "volume" and m.get("Name") in extra for m in mounts):
                raise Stop("additional anonymous volume is referenced")
    return frozenset(extra)


def active(cmd, unit):
    p = subprocess.run([*cmd, "is-active", "--quiet", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if p.returncode not in (0, 3):
        raise Stop("cannot determine unit state")
    return p.returncode == 0


def user_systemctl(uid, username):
    bus = f"/run/user/{uid}/bus"
    if not os.path.exists(bus):
        raise Stop("user systemd bus unavailable")
    return ["runuser", "-u", username, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus}", "systemctl", "--user"]


def require_idle_cron():
    pid = run(["systemctl", "show", "cron.service", "--property=MainPID", "--value"]).decode().strip()
    if not pid.isdecimal():
        raise Stop("cannot inspect cron process")
    if pid != "0":
        p = subprocess.run(["pgrep", "-P", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if p.returncode == 0:
            raise Stop("cron has an in-flight job")
        if p.returncode != 1:
            raise Stop("cannot check cron children")
    group = run(["systemctl", "show", "cron.service", "--property=ControlGroup", "--value"]).decode().strip()
    if group != "/system.slice/cron.service":
        raise Stop("cannot establish cron cgroup")
    path = Path("/sys/fs/cgroup") / group.lstrip("/")
    require_empty_cron_cgroup(path, allowed={pid} if pid != "0" else set(), must_exist=True)
    return path


def require_empty_cron_cgroup(path, allowed=frozenset(), must_exist=False):
    if not path.exists():
        if must_exist:
            raise Stop("cannot inspect cron cgroup")
        return
    try:
        entries = [path / "cgroup.procs", *path.rglob("*/cgroup.procs")]
        for entry in entries:
            for pid in entry.read_text(encoding="ascii").split():
                if not pid.isdecimal() or pid not in allowed:
                    raise Stop("cron cgroup contains an in-flight job")
    except OSError as error:
        raise Stop("cannot inspect cron cgroup") from error


def has_open_writer(lsof_output):
    """Parse lsof field output without logging paths or command arguments."""
    numeric_fd = False
    classified = False
    for line in lsof_output.splitlines():
        if line.startswith(("p", "f")):
            if numeric_fd and not classified:
                return True  # unknown descriptor access is not safely read-only
            numeric_fd = bool(re.match(r"^f\d", line))
            classified = False
        elif line.startswith("a") and numeric_fd:
            classified = True
            if line in ("aw", "au"):
                return True
            if line != "ar":
                return True
    return numeric_fd and not classified


def require_no_writers(home, volume_roots):
    roots = ["/data/docker", "/data/agents", "/data/arr_backup", "/var/lib/personal-ai-brain",
             str(home / ".config" / "personal-ai-brain"), str(home / ".local" / "state" / "personal-ai-brain"),
             str(home / "projects" / "personal-ai-brain"), *volume_roots.values()]
    for root in roots:
        if not os.path.isdir(root):
            raise Stop("expected persistent source missing")
        p = subprocess.run(["lsof", "-nP", "-F", "pfa", "+D", root],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode not in (0, 1) or p.stderr.strip():
            raise Stop("cannot establish persistent-source writer state")
        if has_open_writer(p.stdout.decode(errors="replace")):
            raise Stop("persistent source still has an open writer")


def tree_fingerprint(roots, excluded):
    """Detect changes between two quiesced metadata scans without logging paths."""
    result = hashlib.sha256()
    def failed(error):
        raise Stop("cannot scan a persistent source") from error
    for root in sorted(roots):
        root_stat = os.lstat(root)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise Stop("persistent source is not a directory")
        for parent, dirs, files in os.walk(root, topdown=True, onerror=failed, followlinks=False):
            dirs[:] = sorted(d for d in dirs if os.path.join(parent, d) not in excluded)
            for name in [".", *dirs, *sorted(files)]:
                path = os.path.join(parent, name)
                s = os.lstat(path)
                if s.st_dev != root_stat.st_dev:
                    raise Stop("unexpected nested filesystem in persistent source")
                fields = (path, s.st_mode, s.st_uid, s.st_gid, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                result.update(os.fsencode(repr(fields)) + b"\0")
    return result.digest()


def persistent_tree_fingerprint(home, volume_roots):
    roots = ["/data/docker", "/data/agents", "/data/arr_backup", "/var/lib/personal-ai-brain",
             str(home / ".config" / "personal-ai-brain"), str(home / ".local" / "state" / "personal-ai-brain"),
             str(home / "projects" / "personal-ai-brain"), *volume_roots.values()]
    return tree_fingerprint(roots, {"/data/docker/media", "/data/docker/downloads"})


def create_recovery_record(path, uid, containers, units):
    """Durably claim a transaction before stopping anything; never overwrite."""
    record = {"version": 2, "uid": uid, "boot_id": current_boot_id(),
              "containers": {name: containers[name]["Id"] for name in sorted(containers)},
              "units": sorted(units)}
    validate_recovery_record(record)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(json.dumps(record, sort_keys=True).encode())
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def validate_recovery_record(record):
    if not isinstance(record, dict) or set(record) != {"version", "uid", "boot_id", "containers", "units"}:
        raise Stop("invalid recovery record")
    if record["version"] != 2 or type(record["uid"]) is not int or record["uid"] <= 0 or not re.fullmatch(r"[0-9a-f-]{36}", str(record["boot_id"])):
        raise Stop("invalid recovery record")
    containers, units = record["containers"], record["units"]
    if not isinstance(containers, dict) or set(containers) != EXPECTED_CONTAINERS:
        raise Stop("invalid recovery container set")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in containers.values()):
        raise Stop("invalid recovery container identity")
    allowed = set(SYSTEM_TIMERS + SYSTEM_SERVICES + (USER_TIMER,))
    if not isinstance(units, list) or any(not isinstance(u, str) or u not in allowed for u in units) or len(set(units)) != len(units):
        raise Stop("invalid recovery unit set")


def load_recovery_record(path, owner=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise Stop("unsafe recovery record permissions")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise Stop("recovery record is too large")
        record = json.loads(raw)
        validate_recovery_record(record)
        return record
    finally:
        os.close(fd)


def current_boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", value):
        raise Stop("cannot establish current boot identity")
    return value


def stop_with_deferred_signals(argv):
    """Finish a submitted stop before acting on a catchable interruption."""
    pending = []
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {sig: signal.signal(sig, lambda number, frame: pending.append(number)) for sig in signals}
    try:
        run(argv)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if pending:
        raise Stop("interrupted after stop settled; automatic recovery initiated")


class Quiescence:
    """Restore after settled failures; retain the record for uncertain stops."""
    def __init__(self, uid, username, containers, recovery_path=None):
        self.uid = uid
        self.recovery_path = recovery_path
        self.userctl = user_systemctl(uid, username)
        self.containers = containers
        self.units = []
        self.entered = False
        self.stop_uncertain = False
        self.guard_pid = None
        self.guard_fd = None

    def start_guard(self):
        # A detached child sees EOF if the parent is killed or its TTY drops.
        # It can only restart the exact pre-validated containers and units.
        r, w = os.pipe()
        try:
            pid = os.fork()
        except BaseException:
            os.close(r); os.close(w)
            raise
        if pid == 0:
            os.close(w)
            try:
                os.setsid()
                for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, signal.SIG_IGN)
                null = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(null, fd)
                if null > 2:
                    os.close(null)
                message = os.read(r, 1)
                os.close(r)
                # Parent death leaves the durable record for operator recovery.
                # A detached docker stop may still be active in the daemon.
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(r)
        self.guard_pid, self.guard_fd = pid, w

    def finish_guard(self, restored):
        if self.guard_fd is None:
            return
        try:
            if restored:
                os.write(self.guard_fd, b"D")
        finally:
            os.close(self.guard_fd)
            self.guard_fd = None
        _, status = os.waitpid(self.guard_pid, 0)
        self.guard_pid = None
        if status != 0:
            raise Stop("CRITICAL: independent recovery guard failed")

    def __enter__(self):
        if any(active(["systemctl"], u) for u in SYSTEM_ONESHOTS) or active(self.userctl, USER_ONESHOT):
            raise Stop("a scheduled state writer is still running")
        require_idle_cron()
        candidates = [(["systemctl"], u) for u in (*SYSTEM_TIMERS, *SYSTEM_SERVICES)] + [(self.userctl, USER_TIMER)]
        self.units = [(cmd, unit) for cmd, unit in candidates if active(cmd, unit)]
        if self.recovery_path is not None:
            create_recovery_record(self.recovery_path, self.uid, self.containers,
                                   [unit for _, unit in self.units])
        self.entered = True
        self.start_guard()
        try:
            for cmd, unit in self.units:
                cron_group = None
                if unit == "cron.service":
                    cron_group = require_idle_cron()
                self.stop_uncertain = True
                stop_with_deferred_signals([*cmd, "stop", unit])
                self.stop_uncertain = False
                if cron_group is not None:
                    require_empty_cron_cgroup(cron_group)
            for name in sorted(self.containers):
                self.stop_uncertain = True
                stop_with_deferred_signals(["docker", "stop", "--time", "60", self.containers[name]["Id"]])
                self.stop_uncertain = False
            remaining = run(["docker", "ps", "-q"]).decode().split()
            if remaining:
                raise Stop("a container is still running after quiescence")
            if any(active(["systemctl"], u) for u in SYSTEM_ONESHOTS) or active(self.userctl, USER_ONESHOT):
                raise Stop("a scheduled state writer started during quiescence")
            return self
        except BaseException:
            restored = False
            try:
                if not self.stop_uncertain:
                    self.restore()
                    restored = True
            finally:
                self.finish_guard(restored)
            raise

    def restore(self):
        if not self.entered:
            return
        failures = []
        ignored = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        # Core before control, then dependencies before dependent frontends.
        order = ["personal-ai-brain-core-1", "plex", "prowlarr", "qbittorrent", "sabnzbd",
                 "radarr", "sonarr", "bazarr", "tautulli", "netdata", "seerr",
                 "trading-intelligence-platform-api-1", "personal-ai-brain-control-control-1"]
        try:
            for name in order:
                cid = self.containers[name]["Id"]
                try:
                    state = run(["docker", "inspect", "--format", "{{.State.Running}}", cid]).strip()
                    if state != b"true":
                        run(["docker", "start", cid])
                    if run(["docker", "inspect", "--format", "{{.State.Running}}", cid]).strip() != b"true":
                        raise Stop("container did not restart")
                except (Stop, OSError):
                    failures.append(name)
            for cmd, unit in reversed(self.units):
                try:
                    if not active(cmd, unit):
                        run([*cmd, "start", unit])
                    if not active(cmd, unit):
                        raise Stop("unit did not restart")
                except (Stop, OSError):
                    failures.append(unit)
            # Health checks are only required where the container was healthy
            # before this run; do not silently redefine an existing condition.
            awaiting = {name for name, c in self.containers.items()
                        if c.get("State", {}).get("Health", {}).get("Status") == "healthy" and name not in failures}
            deadline = time.monotonic() + 120
            while awaiting and time.monotonic() < deadline:
                for name in tuple(awaiting):
                    try:
                        value = run(["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                                     self.containers[name]["Id"]]).strip()
                        if value == b"healthy":
                            awaiting.remove(name)
                        elif value == b"unhealthy":
                            awaiting.remove(name)
                            failures.append(name)
                    except (Stop, OSError):
                        awaiting.remove(name)
                        failures.append(name)
                if awaiting:
                    time.sleep(2)
            failures.extend(sorted(awaiting))
            self.entered = False
        finally:
            for sig, handler in ignored.items():
                signal.signal(sig, handler)
        if failures:
            raise Stop("CRITICAL: automatic restart failed: " + ", ".join(failures))

    def __exit__(self, typ, value, tb):
        restored = False
        try:
            self.restore()
            restored = True
        finally:
            self.finish_guard(restored)
        return False


def restore_recorded_services(record, username):
    """Restore recorded active services only; never touch the USB or retry a backup."""
    validate_recovery_record(record)
    containers = {}
    for name, expected_id in record["containers"].items():
        found = docker_inspect(["inspect", name])
        if len(found) != 1 or found[0].get("Id") != expected_id or found[0].get("Name") != "/" + name:
            raise Stop("recorded container identity changed; operator reconciliation required")
        containers[name] = found[0]
    recovery = Quiescence(record["uid"], username, containers)
    recovery.units = [((recovery.userctl if unit == USER_TIMER else ["systemctl"]), unit)
                      for unit in record["units"]]
    recovery.entered = True
    recovery.restore()


def require_no_stop_clients(proc_root=Path('/proc')):
    """Reject visible service-management clients; inaccessible state is unknown."""
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            data = (entry / 'cmdline').read_bytes()
        except FileNotFoundError:
            continue  # process exited during enumeration
        except OSError as error:
            raise Stop("cannot inspect running stop clients") from error
        argv = [os.fsdecode(part) for part in data.split(b'\0') if part]
        if not argv:
            continue
        executable = Path(argv[0]).name
        if executable in {'docker', 'systemctl', 'docker-compose'} and any(
                token in {'stop', 'kill', 'restart', 'down', 'rm'} for token in argv[1:]):
            raise Stop("service-management process still running; operator reconciliation required")


def recovery_preflight(record, username, home):
    validate_recovery_record(record)
    if current_boot_id() == record['boot_id']:
        raise Stop("reboot required before recovery: an earlier stop request may still be pending")
    require_no_stop_clients()
    userctl = user_systemctl(record['uid'], username)
    for cmd in (["systemctl"], userctl):
        if run([*cmd, 'list-jobs', '--no-legend', '--plain']).strip():
            raise Stop("systemd has pending jobs; operator reconciliation required")
    check_disk(home, record['uid'])  # requires the identified USB volume to be unmounted
    for name, expected in record['containers'].items():
        found = docker_inspect(['inspect', name])
        if len(found) != 1 or found[0].get('Id') != expected or found[0].get('Name') != '/' + name:
            raise Stop("recorded container identity changed; operator reconciliation required")
        if found[0].get('State', {}).get('Status') not in ('running', 'exited'):
            raise Stop("container state is not settled; operator reconciliation required")
    for unit in record['units']:
        cmd = userctl if unit == USER_TIMER else ['systemctl']
        state = run([*cmd, 'show', unit, '--property=ActiveState', '--value']).decode().strip()
        if state not in ('active', 'inactive', 'failed'):
            raise Stop("recorded service state is not settled; operator reconciliation required")
    require_no_stop_clients()


def operator_recovery(record_path):
    if pwd is None or os.geteuid() != 0 or not sys.stdin.isatty():
        raise Stop("recovery requires root in an interactive operator TTY")
    verify_root_owned_script()
    lock = acquire_lock()
    try:
        directory = os.lstat(Path(record_path).parent)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) != 0o700:
            raise Stop("unsafe recovery directory")
        uid = int(os.environ.get('SUDO_UID', '0'))
        if uid <= 0:
            raise Stop("recovery must be invoked by the trusted operator through sudo")
        record = load_recovery_record(record_path)
        if record['uid'] != uid:
            raise Stop("recovery record belongs to a different operator")
        user = pwd.getpwuid(uid)
        recovery_preflight(record, user.pw_name, Path(user.pw_dir))
        if input('Type RECOVER to restart previously active services without a backup: ').strip() != 'RECOVER':
            raise Stop("operator declined service recovery")
        # Recheck after operator input, so a new job cannot be silently ignored.
        recovery_preflight(record, user.pw_name, Path(user.pw_dir))
        restore_recorded_services(record, user.pw_name)
        print('SERVICES_RECOVERED: pending record retained; reconcile the USB generation before any future backup.')
        if input('After separately inspecting the USB generation, type RECONCILED to clear only the pending record, or press Enter to keep it: ').strip() == 'RECONCILED':
            recovery_preflight(record, user.pw_name, Path(user.pw_dir))
            for name in record['containers']:
                if docker_inspect(['inspect', name])[0].get('State', {}).get('Running') is not True:
                    raise Stop("a recorded container is no longer running; pending record retained")
            userctl = user_systemctl(uid, user.pw_name)
            if any(not active(userctl if unit == USER_TIMER else ['systemctl'], unit) for unit in record['units']):
                raise Stop("a recorded service is no longer active; pending record retained")
            if load_recovery_record(record_path) != record:
                raise Stop("recovery record changed; operator reconciliation required")
            os.unlink(record_path)
            directory_fd = os.open(str(Path(record_path).parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            print('RECOVERY_RECORD_CLEARED: no backup was retried.')
    finally:
        os.close(lock)


def archive_specs(volume_roots):
    common = ["tar", "--create", "--file=-", "--numeric-owner", "--acls", "--xattrs",
              "--xattrs-include=*", "--sparse", "--one-file-system"]
    root_excludes = ["./data", "./boot", "./var/lib/docker", "./proc", "./sys", "./dev", "./run", "./tmp", "./var/tmp", "./swap.img"]
    specs = [("root.tar.gpg", common + [f"--exclude={p}" for p in root_excludes] + ["-C", "/", "."]),
             ("boot.tar.gpg", common + ["--exclude=./efi", "-C", "/boot", "."]),
             ("efi.tar.gpg", common + ["-C", "/boot/efi", "."]),
             ("docker-data.tar.gpg", common + ["--exclude=./media", "--exclude=./downloads", "-C", "/data/docker", "."]),
             ("agents.tar.gpg", common + ["-C", "/data/agents", "."]),
             ("arr-backup.tar.gpg", common + ["-C", "/data/arr_backup", "."])]
    for name, path in sorted(volume_roots.items()):
        specs.append((f"volume-{name}.tar.gpg", common + ["-C", path, "."]))
    return specs


def require_accounted_root_mounts(tree):
    """Fail closed if one-file-system would silently omit an unplanned mount."""
    excluded = ("/data", "/boot", "/var/lib/docker", "/proc", "/sys",
                "/dev", "/run", "/tmp", "/var/tmp")
    def visit(nodes):
        for node in nodes:
            target = node.get("target")
            if not isinstance(target, str) or not target.startswith("/"):
                raise Stop("cannot establish root mount topology")
            if target != "/" and not any(target == p or target.startswith(p + "/") for p in excluded):
                raise Stop("unaccounted filesystem would be omitted from root archive")
            visit(node.get("children", []))
    if not isinstance(tree, list) or len(tree) != 1 or tree[0].get("target") != "/":
        raise Stop("cannot establish root mount topology")
    visit(tree)


def start_gpg(home, secret, action, output=None, source=None):
    r, w = os.pipe()
    cmd = ["gpg", "--no-options", "--homedir", str(home), "--batch", "--yes", "--no-tty",
           "--pinentry-mode", "loopback", "--no-symkey-cache", "--passphrase-fd", str(r)]
    if action == "encrypt":
        cmd += ["--compress-algo", "none", "--cipher-algo", "AES256", "--output", str(output), "--symmetric"]
    else:
        cmd += ["--decrypt", str(source)]
    child_setup = None
    if action == "encrypt":
        # GPG writes only ciphertext; cap each output before it can exhaust the
        # production filesystem. The reservation is re-evaluated per part.
        import resource
        limit = shutil.disk_usage(Path(output).parent).free - FIVE_GIB
        if limit <= 0:
            os.close(r)
            os.close(w)
            raise Stop("staging filesystem would breach its five-GiB reserve")
        def child_setup():
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if action == "encrypt" else subprocess.DEVNULL,
                             stdout=subprocess.PIPE if action == "decrypt" else subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, pass_fds=(r,), preexec_fn=child_setup)
        os.close(r)
        r = None
        os.write(w, secret + b"\n")
    finally:
        if r is not None:
            os.close(r)
        os.close(w)
    return p


def encrypt_command(stage, home, secret, name, argv):
    dst = stage / name
    if dst.exists() or dst.is_symlink():
        raise Stop("refusing to overwrite staging part")
    gpg = start_gpg(home, secret, "encrypt", output=dst)
    producer = None
    try:
        producer = subprocess.Popen(argv, stdout=gpg.stdin, stderr=subprocess.DEVNULL)
        gpg.stdin.close()
        prod_rc = producer.wait()
        gpg_rc = gpg.wait()
        if prod_rc or gpg_rc:
            raise Stop("archive or encryption failed; staging is retained")
    except BaseException:
        if producer is not None and producer.poll() is None:
            producer.kill()
            producer.wait()
        if gpg.poll() is None:
            gpg.kill()
        gpg.wait()
        raise
    os.chmod(dst, 0o600)
    return dst


def encrypt_bytes(stage, home, secret, name, data):
    dst = stage / name
    if dst.exists() or dst.is_symlink():
        raise Stop("refusing to overwrite staging part")
    p = start_gpg(home, secret, "encrypt", output=dst)
    try:
        p.communicate(data)
    except BaseException:
        if p.poll() is None:
            p.kill()
            p.wait()
        raise
    if p.returncode:
        raise Stop("encryption failed")
    os.chmod(dst, 0o600)
    return dst


def snapshot_radarr(stage, home, secret, path=Path("/data/docker/radarr/radarr.db")):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 512 * 1024**2:
        raise Stop("representative database missing or too large for memory-only verification")
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(":memory:") as dst:
        dst.execute("PRAGMA temp_store=MEMORY")
        deadline = time.monotonic() + 30
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise Stop("representative database snapshot timed out")
        src.backup(dst, pages=256, progress=progress, sleep=0.05)
        if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise Stop("representative database integrity check failed")
        data = dst.serialize()
    return encrypt_bytes(stage, home, secret, "radarr-snapshot.sqlite.gpg", data)


def verify_gpg(home, secret, path, kind):
    p = start_gpg(home, secret, "decrypt", source=path)
    try:
        if kind == "tar":
            t = subprocess.Popen(["tar", "--list", "--file=-"], stdin=p.stdout,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.stdout.close()
            tar_rc = t.wait()
            gpg_rc = p.wait()
            if tar_rc or gpg_rc:
                raise Stop("encrypted archive failed decrypt/list verification")
        elif kind == "sqlite":
            data = p.stdout.read(512 * 1024**2 + 1)
            p.stdout.close()
            if p.wait() or len(data) > 512 * 1024**2:
                raise Stop("representative database decrypt failed")
            with sqlite3.connect(":memory:") as con:
                con.execute("PRAGMA temp_store=MEMORY")
                con.deserialize(data)
                if con.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise Stop("isolated representative database integrity failed")
        else:
            while p.stdout.read(1024 * 1024):
                pass
            p.stdout.close()
            if p.wait():
                raise Stop("encrypted metadata decrypt failed")
    except BaseException:
        if p.poll() is None:
            p.kill()
            p.wait()
        raise


def verify_radarr_config(home, secret, archive):
    """Inspect the restored XML in memory; never write user credentials to scratch."""
    p = start_gpg(home, secret, "decrypt", source=archive)
    found = 0
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|") as tar:
            for item in tar:
                if item.name.removeprefix("./") != "radarr/config.xml":
                    continue
                found += 1
                if found != 1 or not item.isfile() or item.size > 1024 * 1024:
                    raise Stop("representative configuration is unsafe or duplicated")
                stream = tar.extractfile(item)
                if stream is None:
                    raise Stop("representative configuration cannot be read")
                try:
                    root = ET.fromstring(stream.read(1024 * 1024 + 1))
                except ET.ParseError:
                    raise Stop("representative configuration is invalid XML") from None
                if root.tag != "Config":
                    raise Stop("representative configuration has an unexpected root")
        while p.stdout.read(1024 * 1024):
            pass
        p.stdout.close()
        if p.wait() or found != 1:
            raise Stop("representative configuration restore failed")
    except (tarfile.TarError, OSError):
        raise Stop("representative archive could not be parsed") from None
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def synthetic_radarr_smoke(image):
    """Boot a disposable, networkless Radarr with a synthetic tmpfs config.

    Actual recovered DB and config are verified separately in memory: no
    long-lived user credentials are copied into the disposable filesystem.
    """
    name = "issue33-restore-fixture-" + uuid.uuid4().hex
    cid = None
    try:
        cid = run(["docker", "run", "-d", "--pull", "never", "--name", name,
                   "--label", "issue33.synthetic=1", "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--pids-limit", "128", "--memory", "512m", "--log-driver", "none",
                   "--tmpfs", "/config:rw,nosuid,nodev,noexec,size=256m",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                   "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
                   "-e", "HOME=/tmp", "-e", "DOTNET_CLI_HOME=/tmp",
                   "--entrypoint", "/app/radarr/bin/Radarr", image,
                   "-nobrowser", "-data=/config"]).decode().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise Stop("isolated fixture did not return a container ID")
        metadata = docker_inspect(["inspect", cid])
        if (len(metadata) != 1 or metadata[0].get("Config", {}).get("Labels", {}).get("issue33.synthetic") != "1"
                or metadata[0].get("HostConfig", {}).get("NetworkMode") != "none"
                or not metadata[0].get("HostConfig", {}).get("ReadonlyRootfs")
                or any(m.get("Type") != "tmpfs" for m in metadata[0].get("Mounts", []))):
            raise Stop("isolated fixture safety conditions were not met")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            ping = subprocess.run(["docker", "exec", cid, "curl", "--noproxy", "*", "-fsS", "--max-time", "2",
                                   "http://localhost:7878/ping"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ping.returncode == 0:
                return
            if run(["docker", "inspect", "--format", "{{.State.Running}}", cid]).strip() != b"true":
                break
            time.sleep(2)
        raise Stop("isolated synthetic Radarr failed to start")
    finally:
        if not cid:
            # The Docker client can fail after the daemon created the fixture.
            # The random exact name plus label prevents touching other containers.
            found = run(["docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + name + "$",
                         "--filter", "label=issue33.synthetic=1"]).decode().split()
            if len(found) > 1:
                raise Stop("CRITICAL: synthetic fixture ownership is ambiguous")
            if found:
                cid = found[0]
        if cid and not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise Stop("CRITICAL: synthetic fixture ID is invalid")
        if cid and re.fullmatch(r"[0-9a-f]{64}", cid):
            meta = docker_inspect(["inspect", cid])
            if len(meta) != 1 or meta[0].get("Config", {}).get("Labels", {}).get("issue33.synthetic") != "1":
                raise Stop("CRITICAL: isolated fixture ownership cannot be confirmed")
            run(["docker", "rm", "--force", cid])


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def copy_exclusive(source, target):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
        shutil.copyfileobj(inp, out, 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    if os.path.getsize(source) != os.path.getsize(target) or digest(source) != digest(target):
        raise Stop("destination part did not match staging")


def verify_root_owned_script():
    p = Path(__file__).resolve()
    s = p.stat()
    if s.st_uid != 0 or (s.st_mode & 0o022):
        raise Stop("script must be reviewed, root-owned and not group/world writable")


def acquire_lock():
    import fcntl
    fd = os.open("/run/lock/issue33-backup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(fd)
        raise Stop("backup lock is unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Stop("another backup transaction is running")
    return fd


def expected_part_names():
    base = {"root.tar.gpg", "boot.tar.gpg", "efi.tar.gpg", "docker-data.tar.gpg", "agents.tar.gpg",
            "arr-backup.tar.gpg", "docker-images.tar.gpg", "containers.json.gpg", "volumes.json.gpg",
            "networks.json.gpg", "packages.txt.gpg", "source-manifest.json.gpg", "radarr-snapshot.sqlite.gpg"}
    return base | {f"volume-{v}.tar.gpg" for v in EXPECTED_VOLUMES} | {f"container-{n}.tar.gpg" for n in EXPECTED_CONTAINERS}


def disable_core_dumps():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        raise Stop("cannot disable core dumps")


def verify_generation(path, secret):
    """Read-only verification and in-memory Radarr restore on an isolated Linux host."""
    directory = Path(path)
    if directory.is_symlink() or not directory.is_dir():
        raise Stop("generation must be an existing non-symlink directory")
    checksum_file = directory / "checksums.json.gpg"
    if checksum_file.is_symlink() or not checksum_file.is_file():
        raise Stop("encrypted checksums are missing")
    with tempfile.TemporaryDirectory(prefix="issue33-verify-") as tmp:
        home = Path(tmp) / "gpg"
        home.mkdir(mode=0o700)
        try:
            p = start_gpg(home, secret, "decrypt", source=checksum_file)
            data = p.stdout.read(1024 * 1024 + 1)
            p.stdout.close()
            if p.wait() or len(data) > 1024 * 1024:
                raise Stop("cannot decrypt bounded checksums")
            expected = json.loads(data)
            if not isinstance(expected, dict) or not expected:
                raise Stop("invalid checksums")
            required = set(expected)
            if required != expected_part_names():
                raise Stop("generation does not cover the expected snapshot parts")
            if any(not re.fullmatch(r"[A-Za-z0-9_.-]+\.gpg", n) or n.startswith(".") for n in required):
                raise Stop("unsafe archive name")
            actual = {p.name for p in directory.iterdir()}
            if actual != required | {"checksums.json.gpg"}:
                raise Stop("generation contents differ from encrypted checksums")
            for name, metadata in expected.items():
                part = directory / name
                if part.is_symlink() or not part.is_file() or not isinstance(metadata, dict):
                    raise Stop("invalid archive part")
                if part.stat().st_size != metadata.get("bytes") or digest(part) != metadata.get("sha256"):
                    raise Stop("encrypted archive hash or size mismatch")
                kind = "tar" if name.endswith(".tar.gpg") else "sqlite" if name.endswith(".sqlite.gpg") else "metadata"
                verify_gpg(home, secret, part, kind)
            verify_radarr_config(home, secret, directory / "docker-data.tar.gpg")
        finally:
            subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="run the separately approved privileged transaction")
    ap.add_argument("--recover-services", action="store_true", help="recover recorded services after reboot without retrying a backup")
    ap.add_argument("--generation", help="explicit new .homelab-issue33-<32 lowercase hex> directory for a later run")
    ap.add_argument("--verify-generation", help="read-only verify a mounted generation on an isolated Linux host")
    args = ap.parse_args()
    if args.recover_services:
        if args.execute or args.generation or args.verify_generation:
            raise Stop("recovery cannot be combined with other actions")
        operator_recovery(Path('/var/lib/issue33-backup/pending.json'))
        return
    if args.verify_generation:
        if args.execute or args.generation or os.name != "posix" or not sys.stdin.isatty():
            raise Stop("verification needs a Linux operator TTY and cannot be combined with execution")
        disable_core_dumps()
        phrase = getpass.getpass("Recovery passphrase (not logged): ")
        if not phrase or "\n" in phrase or len(phrase.encode()) > 512:
            raise Stop("invalid passphrase")
        secret = phrase.encode()
        del phrase
        verify_generation(args.verify_generation, secret)
        print("SNAPSHOT_VERIFIED: archives and in-memory Radarr integrity passed.")
        print("LIMITATION: application startup and whole-host restoration were not tested.")
        return
    if not args.execute:
        raise Stop("no action: the separately approved --execute flag is required")
    if pwd is None or not hasattr(os, "geteuid") or os.geteuid() != 0 or not sys.stdin.isatty():
        raise Stop("run as root in an interactive operator TTY")
    verify_root_owned_script()
    lock_fd = acquire_lock()
    disable_core_dumps()
    uid = int(os.environ.get("SUDO_UID", "0"))
    if uid <= 0:
        raise Stop("must be invoked by the trusted operator through sudo")
    user = pwd.getpwuid(uid)
    home = Path(user.pw_dir)
    os.umask(0o077)
    recovery_dir = Path("/var/lib/issue33-backup")
    recovery_dir.mkdir(mode=0o700, exist_ok=True)
    recovery_stat = os.lstat(recovery_dir)
    if not stat.S_ISDIR(recovery_stat.st_mode) or recovery_stat.st_uid != 0 or stat.S_IMODE(recovery_stat.st_mode) != 0o700:
        raise Stop("unsafe recovery directory")
    recovery_path = recovery_dir / "pending.json"
    if os.path.lexists(recovery_path):
        load_recovery_record(recovery_path)
        raise Stop("unfinished operation requires operator recovery; no backup will be retried")
    profile, profile_bytes = select_profile(home, uid)
    generation_name = args.generation or profile.get("proposal_2026_11_06", {}).get("target", {}).get("generation_directory_at_volume_root", "")
    if not re.fullmatch(r"\.homelab-issue33-[0-9a-f]{32}", generation_name):
        raise Stop("private profile has no preselected unique generation name")
    device = check_disk(home, uid)
    containers = validate_workload(profile)
    excluded_volumes = validate_volumes()
    if shutil.disk_usage("/data").free < MIN_SOURCE_RESERVE + FIVE_GIB:
        raise Stop("insufficient staging space")
    if input("Type STORED to confirm off-host custody and independent recovery test: ").strip() != "STORED":
        raise Stop("off-host passphrase custody was not confirmed")
    if input("Type IDLE to confirm no in-flight jobs and approved maintenance: ").strip() != "IDLE":
        raise Stop("maintenance readiness not confirmed")
    phrase = getpass.getpass("Recovery passphrase (not logged): ")
    repeat = getpass.getpass("Repeat passphrase: ")
    if phrase != repeat or len(phrase) < 20 or "\n" in phrase or len(phrase.encode()) > 512:
        raise Stop("passphrases do not match or are too short")
    secret = phrase.encode()
    del phrase, repeat
    stage = Path(tempfile.mkdtemp(prefix=".issue33-encrypted-", dir="/data"))
    os.chmod(stage, 0o700)
    gpg_home = stage / ".gpg"
    gpg_home.mkdir(mode=0o700)
    previous_handlers = {}
    def interrupted(signum, frame):
        raise Stop("interrupted; automatic recovery initiated")
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[sig] = signal.signal(sig, interrupted)
    mount_dir = None
    mounted = False
    completed = False
    try:
        sentinel = b"issue33-recovery-test-v1"
        test = encrypt_bytes(stage, gpg_home, secret, "recovery-test.gpg", sentinel)
        p = start_gpg(gpg_home, secret, "decrypt", source=test)
        recovered = p.stdout.read(256)
        p.stdout.close()
        if p.wait() or recovered != sentinel:
            raise Stop("passphrase round-trip failed")
        volume_roots = {v["name"]: f"/var/lib/docker/volumes/{v['name']}/_data" for v in profile["volumes"]}
        parts = []
        with Quiescence(uid, user.pw_name, containers, recovery_path):
            mount_tree = json.loads(run(["findmnt", "-J", "-R", "-o", "TARGET", "/"]))
            require_accounted_root_mounts(mount_tree.get("filesystems"))
            require_no_writers(home, volume_roots)
            before = persistent_tree_fingerprint(home, volume_roots)
            for name, argv in archive_specs(volume_roots):
                parts.append((encrypt_command(stage, gpg_home, secret, name, argv), "tar"))
            images = sorted({c["Image"] for c in containers.values()})
            parts.append((encrypt_command(stage, gpg_home, secret, "docker-images.tar.gpg", ["docker", "image", "save", *images]), "tar"))
            for name in sorted(containers):
                parts.append((encrypt_command(stage, gpg_home, secret, f"container-{name}.tar.gpg", ["docker", "export", containers[name]["Id"]]), "tar"))
            parts.append((encrypt_command(stage, gpg_home, secret, "containers.json.gpg", ["docker", "inspect", *[c["Id"] for c in containers.values()]]), "metadata"))
            parts.append((encrypt_command(stage, gpg_home, secret, "volumes.json.gpg", ["docker", "volume", "inspect", *sorted(EXPECTED_VOLUMES)]), "metadata"))
            networks = run(["docker", "network", "ls", "-q"]).decode().split()
            if not networks:
                raise Stop("Docker network inventory unexpectedly empty")
            parts.append((encrypt_command(stage, gpg_home, secret, "networks.json.gpg", ["docker", "network", "inspect", *networks]), "metadata"))
            parts.append((encrypt_command(stage, gpg_home, secret, "packages.txt.gpg", ["dpkg-query", "-W"]), "metadata"))
            parts.append((encrypt_bytes(stage, gpg_home, secret, "source-manifest.json.gpg", profile_bytes), "metadata"))
            parts.append((snapshot_radarr(stage, gpg_home, secret), "sqlite"))
            require_no_writers(home, volume_roots)
            if validate_volumes() != excluded_volumes:
                raise Stop("excluded volume inventory changed during capture")
            if persistent_tree_fingerprint(home, volume_roots) != before:
                raise Stop("persistent source changed during the snapshot")
        # Services are back before any USB mount or copy.
        if {part.name for part, _ in parts} != expected_part_names():
            raise Stop("staging does not cover all expected parts")
        checksums = {part.name: {"bytes": part.stat().st_size, "sha256": digest(part)} for part, _ in parts}
        parts.append((encrypt_bytes(stage, gpg_home, secret, "checksums.json.gpg", json.dumps(checksums, sort_keys=True).encode()), "metadata"))
        for part, kind in parts:
            verify_gpg(gpg_home, secret, part, kind)
        verify_radarr_config(gpg_home, secret, stage / "docker-data.tar.gpg")
        synthetic_radarr_smoke(containers["radarr"]["Image"])
        if check_disk(home, uid) != device:
            raise Stop("USB device path changed during staging")
        mount_dir = Path(tempfile.mkdtemp(prefix="issue33-usb-", dir="/run"))
        run(["mount", "-t", "ntfs-3g", "-o", "rw,norecover,nodev,nosuid,noexec", device[1], str(mount_dir)])
        mounted = True
        check_mounted_disk(home, uid, mount_dir, *device)
        needed = sum(p.stat().st_size for p, _ in parts) + FIVE_GIB
        if shutil.disk_usage(mount_dir).free < needed:
            raise Stop("USB free space no longer meets actual encrypted size plus reserve")
        generation = mount_dir / generation_name
        generation.mkdir(mode=0o700)  # exclusive; existing contents are never touched
        for part, kind in parts:
            check_mounted_disk(home, uid, mount_dir, *device)
            if shutil.disk_usage(mount_dir).free < part.stat().st_size + FIVE_GIB:
                raise Stop("USB reserve would be breached during copy")
            dst = generation / part.name
            copy_exclusive(part, dst)
            verify_gpg(gpg_home, secret, dst, kind)
        completed = True
        print("SNAPSHOT_VERIFIED: encrypted parts, in-memory Radarr DB/config and synthetic isolated startup passed.")
        print("LIMITATION: Radarr did not start with the restored DB/config; whole-host restoration was not tested.")
        print("Encrypted staging and USB generation are retained; no pruning was performed.")
    finally:
        cleanup_error = None
        # A signal can arrive just after mount(8) succeeds and before the
        # assignment to mounted. The unique directory is ours, so inspect it.
        if mount_dir and (mounted or os.path.ismount(mount_dir)):
            try:
                run(["umount", str(mount_dir)])
            except Stop:
                cleanup_error = "CRITICAL: USB unmount failed; operator intervention required"
        if mount_dir and not os.path.ismount(mount_dir):
            try:
                os.rmdir(mount_dir)
            except OSError:
                cleanup_error = cleanup_error or "temporary mountpoint could not be removed"
        subprocess.run(["gpgconf", "--homedir", str(gpg_home), "--kill", "gpg-agent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if completed and not cleanup_error:
            load_recovery_record(recovery_path)
            os.unlink(recovery_path)
            directory_fd = os.open(str(recovery_dir), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        os.close(lock_fd)
        if cleanup_error:
            raise Stop(cleanup_error)


if __name__ == "__main__":
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as e:
        print(f"STOP: {e}", file=sys.stderr)
        raise SystemExit(1)
