"""Operator-only atomic publication of a sanitized Movie Intelligence aggregate.

This is NOT a service, scheduler or automatic host deployment. The output path
is a fixed operator-owned private host location, NEVER a model/caller selector.
No database, Plex, Radarr, Seerr, history or download operation is performed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import stat
import tempfile

RESOURCE_ID = "movies/watchlist-overview"
DEFAULT_HOST_SNAPSHOT_PATH = Path(
    "/var/lib/personal-ai-brain/media-audit/movies-watchlist-overview.json"
)
MAX_SNAPSHOT_BYTES = 4096
MAX_SOURCE_AGE = timedelta(minutes=30)
MAX_FUTURE_SKEW = timedelta(minutes=2)


class SnapshotUnavailable(ValueError):
    """A fixed public error; never leak private source paths or values."""


def _deny():
    raise SnapshotUnavailable("movie_snapshot_unavailable")


def _count_group(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _deny()
    if any(type(value[k]) is not int or not 0 <= value[k] <= 1_000_000 for k in fields):
        _deny()


def validate_aggregate(packet, *, now=None):
    """Enforce the same exact bounded v0.1 wire fields as the PAB reader.

    The consumer additionally validates the entire packet again on each read.
    """
    if (type(packet) is not dict or set(packet) != {
        "schema_version", "resource_id", "collected_at", "complete",
        "source_status", "catalog", "disposition", "plex"
    } or packet["schema_version"] != "0.1" or packet["resource_id"] != RESOURCE_ID
            or packet["complete"] is not True):
        _deny()
    stamp = packet["collected_at"]
    if type(stamp) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp
    ):
        _deny()
    try:
        collected_at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        age = (now or datetime.now(timezone.utc)) - collected_at
    except (ValueError, TypeError, OverflowError):
        _deny()
    if not -MAX_FUTURE_SKEW <= age <= MAX_SOURCE_AGE:
        _deny()
    status = packet["source_status"]
    if type(status) is not dict or set(status) != {"movie_db", "plex", "radarr", "seerr"} or any(
        status[key] != "ok" for key in status
    ):
        _deny()
    catalog, disposition, plex = (packet[k] for k in ("catalog", "disposition", "plex"))
    _count_group(catalog, ("total", "watched", "watchlist", "overlap"))
    _count_group(disposition, (
        "playable", "managed_or_requested", "reviewable_missing", "unresolved",
    ))
    _count_group(plex, ("playable_movies",))
    if (any(catalog[k] > catalog["total"] for k in ("watched", "watchlist", "overlap"))
            or catalog["overlap"] > min(catalog["watched"], catalog["watchlist"])
            or catalog["watched"] + catalog["watchlist"] - catalog["overlap"] > catalog["total"]
            or sum(disposition.values()) != catalog["watchlist"]
            or disposition["playable"] > plex["playable_movies"]):
        _deny()
    try:
        raw = (json.dumps(packet, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        _deny()
    if len(raw) > MAX_SNAPSHOT_BYTES:
        _deny()
    return raw


def publish_private_snapshot(packet, path: Path = DEFAULT_HOST_SNAPSHOT_PATH, *, now=None):
    """Atomic one-file 0600 replacement, only in existing 0700 operator directory.

    No mkdir/chown/mount/privilege changes, and no public output or retained
    copy. The operator must independently verify producer/consumer UID parity
    and a read-only control-container bind before enabling PAB.
    """
    pending = None
    try:
        if type(path) is not Path or not path.is_absolute():
            _deny()
        raw = validate_aggregate(packet, now=now)
        directory = path.parent
        info = directory.lstat()  # No following a symlink parent.
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            _deny()
        try:
            target = path.lstat()
        except FileNotFoundError:
            target = None
        if target is not None and (
            not stat.S_ISREG(target.st_mode) or target.st_uid != os.geteuid()
            or target.st_nlink != 1 or target.st_mode & 0o077
        ):
            _deny()
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".movie-snapshot-", delete=False
        ) as stream:
            pending = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        pending = None
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, RecursionError):
        _deny()
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
