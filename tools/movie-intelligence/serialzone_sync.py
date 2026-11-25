"""Read-only SerialZone collection and isolated source-state import (issue #66)."""
import argparse
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

ORIGIN = "https://www.serialzone.cz"
AGENT = "PersonalMediaSync/1.0"
SECTIONS = {
    "ratings": ("hodnoceni/", r"ohodnoceno\s+([\d ]+)\s+seriál"),
    "following": ("moje/", r"sleduje\s+([\d ]+)\s+seriál"),
    "progress": ("rozkoukane/", r"Dokoukaných\s+[\d ]+\s+z\s+([\d ]+)\s+seriál"),
    "completed": ("dokoukane/", r"má\s*([\d ]+)\s+dokoukaných seriál"),
    "watchlist": ("chci-videt/", r"chce vidět\s+([\d ]+)\s+seriál"),
}
MAX_PAGES = 20
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10000
ROW = "def-list2"
SCHEMA = {
    "serialzone_series_observations": (
        "section", "slug", "title", "original_title", "year", "rating_percent",
        "rated_on", "added_on", "favourite", "ignored", "completed_marker",
        "episodes_watched", "episodes_total", "source_url"
    ),
    "serialzone_sync_state": ("singleton", "observed_at", "digest", "counts_json"),
}
DDL = (
    """CREATE TABLE serialzone_series_observations (
      section TEXT NOT NULL CHECK(section IN ('ratings','following','progress','completed','watchlist')),
      slug TEXT NOT NULL, title TEXT NOT NULL, original_title TEXT, year INTEGER,
      rating_percent INTEGER CHECK(rating_percent BETWEEN 0 AND 100),
      rated_on TEXT, added_on TEXT, favourite INTEGER, ignored INTEGER,
      completed_marker INTEGER, episodes_watched INTEGER, episodes_total INTEGER,
      source_url TEXT NOT NULL, PRIMARY KEY(section, slug)) WITHOUT ROWID""",
    """CREATE TABLE serialzone_sync_state (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      observed_at TEXT NOT NULL, digest TEXT NOT NULL, counts_json TEXT NOT NULL)""",
    "CREATE INDEX serialzone_observations_slug ON serialzone_series_observations(slug)",
)

class SyncError(Exception):
    pass

def clean(value):
    return " ".join(value.split())

def integer(value):
    return int(value.replace(" ", ""))

def date_value(value):
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*", value)
    if not match:
        raise SyncError("invalid source date")
    day, month, year = map(int, match.groups())
    return dt.date(year, month, day).isoformat()

def profile_url(url):
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc) != ("https", "www.serialzone.cz") or parts.query or parts.fragment:
        raise SyncError("invalid profile URL")
    if not re.fullmatch(r"/uzivatele/[1-9]\d*-[A-Za-z0-9_-]+/", parts.path):
        raise SyncError("invalid profile path")
    return ORIGIN + parts.path

class Page(HTMLParser):
    """Capture explicit list rows and navigation; never execute JS."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.header = []
        self.rows = []
        self.row = None
        self.depth = 0
        self.field = None
        self.forward = False
        self.next_link = None
        self.feed(html)
        self.close()
        if self.row is not None:
            raise SyncError("truncated row")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "div" and "pfor" in classes:
            self.forward = True
        if self.forward and tag == "a":
            if self.next_link is not None:
                raise SyncError("multiple next links")
            self.next_link = a.get("href")
        if tag == "div" and ROW in classes:
            if self.row is not None:
                raise SyncError("nested list row")
            self.row = {"classes": classes, "all": [], "fields": {}}
            self.depth = 1
            return
        if self.row is None:
            return
        if tag == "div":
            self.depth += 1
        for name in ("dfl2-name", "dfl2-2ndrow", "ins-score"):
            if name in classes:
                if name in self.row["fields"]:
                    raise SyncError("duplicate row field")
                self.field = (name, tag)
                self.row["fields"][name] = [a, []]

    def handle_endtag(self, tag):
        if tag == "div":
            self.forward = False
        if self.row is None:
            return
        if self.field and self.field[1] == tag:
            self.field = None
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.rows.append(self.row)
                self.row = None
                self.field = None

    def handle_data(self, data):
        if self.row is not None:
            self.row["all"].append(data)
            if self.field:
                self.row["fields"][self.field[0]][1].append(data)
        elif not self.rows:
            self.header.append(data)

def parse_row(row, section):
    fields = row["fields"]
    if "dfl2-name" not in fields:
        raise SyncError("missing title")
    attrs, text = fields["dfl2-name"]
    url = urljoin(ORIGIN, attrs.get("href") or "")
    parts = urlsplit(url)
    match = re.fullmatch(r"/serial/([a-zA-Z0-9_-]+)/", parts.path)
    if (parts.scheme, parts.netloc) != ("https", "www.serialzone.cz") or not match or parts.query or parts.fragment:
        raise SyncError("invalid series URL")
    slug = match.group(1)
    title = clean("".join(text))
    year = None
    suffix = re.search(r"\s*\((\d{4})(?:\s*[-–]\s*(?:\d{4}|\?{4}))?\)$", title)
    if suffix:
        year = int(suffix.group(1))
        title = title[:suffix.start()].strip()
    if not title:
        raise SyncError("empty title")
    original = clean("".join(fields.get("dfl2-2ndrow", ({}, []))[1])) or None
    all_text = clean(" ".join(row["all"]))
    rating = rated_on = added_on = favourite = ignored = completed_marker = watched = total = None
    if section == "ratings":
        a, value = fields.get("ins-score", ({}, []))
        m = re.fullmatch(r"(\d{1,3})\s*%", clean("".join(value)))
        if not m or int(m.group(1)) > 100:
            raise SyncError("invalid personal rating")
        rating = int(m.group(1))
        rated_on = date_value(a.get("title"))
    if section in ("following", "watchlist"):
        m = re.search(r"\bod\s+(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", all_text)
        added_on = date_value(m.group(1)) if m else None
    if section == "following":
        classes = row["classes"]
        for positive, negative in (("filtr-love", "filtr-nelove"), ("filtr-ignor", "filtr-neignor")):
            if (positive in classes) == (negative in classes):
                raise SyncError("unknown following flags")
        favourite = int("filtr-love" in classes)
        ignored = int("filtr-ignor" in classes)
        completed_marker = int("dokoukaný" in all_text)
    if section == "progress":
        m = re.search(r"\bstav\s+([\d ]+)/\s*([\d ]+)", all_text)
        if not m:
            raise SyncError("missing episode progress")
        watched, total = map(integer, m.groups())
        if not 0 < watched <= total:
            raise SyncError("invalid episode progress")
    if section in ("completed", "watchlist"):
        m = re.search(r"\b([\d ]+)\s+epizod", all_text)
        if m:
            total = integer(m.group(1).strip())
        if section == "completed":
            if total is None or total <= 0:
                raise SyncError("invalid completed count")
            watched = total
    return (section, slug, title, original, year, rating, rated_on, added_on,
            favourite, ignored, completed_marker, watched, total, url)

def collect(base, fetch):
    base = profile_url(base)
    records = []
    counts = {}
    for section, (path, pattern) in SECTIONS.items():
        first = base + path
        url = first
        seen = set()
        expected = None
        for index in range(1, MAX_PAGES + 1):
            page = Page(fetch(url))
            if index == 1:
                header = clean(" ".join(page.header))
                m = re.search(pattern, header, re.I)
                if not m:
                    raise SyncError("missing section count: " + section)
                expected = integer(m.group(1).strip())
                if expected > MAX_ROWS:
                    raise SyncError("section too large")
            for item in page.rows:
                record = parse_row(item, section)
                if record[1] in seen:
                    raise SyncError("duplicate series in section")
                seen.add(record[1])
                records.append(record)
            if len(seen) > expected:
                raise SyncError("section count exceeded")
            if not page.next_link:
                break
            next_url = urljoin(url, page.next_link)
            parts = urlsplit(next_url)
            if (parts.scheme, parts.netloc) != ("https", "www.serialzone.cz") or parts.fragment:
                raise SyncError("invalid next origin")
            if parts.path != urlsplit(first).path + f"strana-{index+1}/":
                raise SyncError("unexpected next page")
            if parts.query not in ("", "razeni=datum"):
                raise SyncError("unexpected pagination query")
            url = next_url
        else:
            raise SyncError("page limit exceeded")
        if len(seen) != expected:
            raise SyncError("incomplete section: " + section)
        counts[section] = expected
    # A profile with no rows is deliberately not auto-applied.
    if not records:
        raise SyncError("all sections empty")
    return sorted(records), counts

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Fetcher:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.last = 0.0
        self.robots = RobotFileParser()
        self.robots.parse(self.read(ORIGIN + "/robots.txt").splitlines())

    def read(self, url):
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != ("https", "www.serialzone.cz"):
            raise SyncError("invalid fetch origin")
        time.sleep(max(0, 2 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = Request(url, headers={"User-Agent": AGENT, "Accept": "text/html,text/plain"})
        with self.opener.open(req, timeout=15) as response:
            if response.status != 200:
                raise SyncError("unexpected HTTP status")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise SyncError("response exceeds bound")
            return raw.decode("utf-8")

    def __call__(self, url):
        if not self.robots.can_fetch(AGENT, url):
            raise SyncError("site robots policy disallows import")
        return self.read(url)

def infer_prefix(count, verified_episode_order, exact_states=None):
    """Return inferred source membership only; not actual playback events.

    The caller must supply independently verified, complete episode ordering.
    Exact evidence (including explicit unwatched) takes precedence.
    """
    order = tuple(verified_episode_order)
    if type(count) is not int or count < 0 or count > len(order):
        raise SyncError("insufficient episode catalogue")
    if any(not isinstance(x, str) or not x for x in order) or len(set(order)) != len(order):
        raise SyncError("ambiguous episode catalogue")
    exact_states = exact_states or {}
    return [
        {"episode_id": item, "provenance": "inferred_sequential", "watched_at": None}
        for item in order[:count] if item not in exact_states
    ]

def check_schema(conn, initialize=False):
    tables = {r[0]: r[1] for r in conn.execute("SELECT name,type FROM sqlite_master")}
    for name, required in {
        "movies": {"id", "title", "original_title", "year"},
        "user_movies": {"movie_id", "watched", "watchlist"},
        "external_ids": {"movie_id", "source", "external_id"},
    }.items():
        if tables.get(name) != "table" or not required <= {r[1] for r in conn.execute("PRAGMA table_info(" + name + ")")}:
            raise SyncError("existing media.db schema not recognized")
    present = set(SCHEMA) & set(tables)
    if not present and initialize:
        return False
    if present != set(SCHEMA):
        raise SyncError("SerialZone schema not initialized or incomplete")
    for name, columns in SCHEMA.items():
        if tables[name] != "table" or tuple(r[1] for r in conn.execute("PRAGMA table_info(" + name + ")")) != columns:
            raise SyncError("unexpected SerialZone schema")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?,?)", tuple(SCHEMA)).fetchone():
        raise SyncError("unexpected SerialZone trigger")
    return True

def digest_rows(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def apply_snapshot(db, rows, counts, observed_at, initialize=False, backup_dir=None):
    path = Path(db).resolve(strict=True)
    if not path.is_file():
        raise SyncError("existing database required")
    digest = digest_rows(rows)
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    backup_path = None
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        exists = check_schema(conn, initialize)
        if initialize and not exists:
            if not backup_dir:
                raise SyncError("initialization requires private backup directory")
            directory = Path(backup_dir).resolve(strict=True)
            if not directory.is_dir() or directory.stat().st_mode & 0o077:
                raise SyncError("backup directory must be private")
            fd, name = tempfile.mkstemp(prefix="media-before-serialzone-", suffix=".db", dir=directory)
            os.close(fd)
            backup_path = name
            with sqlite3.connect(name) as backup:
                conn.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise SyncError("backup integrity failed")
        conn.execute("BEGIN IMMEDIATE")
        if not check_schema(conn, initialize):
            if backup_path is None:
                raise SyncError("backup required")
            for statement in DDL:
                conn.execute(statement)
        previous = conn.execute("SELECT observed_at,digest FROM serialzone_sync_state WHERE singleton=1").fetchone()
        if previous and previous[0] > observed_at:
            raise SyncError("stale snapshot")
        changed = previous is None or previous[1] != digest
        if changed:
            # Replace only this source's current projection, never canonical history.
            conn.execute("DELETE FROM serialzone_series_observations")
            conn.executemany("INSERT INTO serialzone_series_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        actual = conn.execute("SELECT * FROM serialzone_series_observations ORDER BY section,slug").fetchall()
        if digest_rows(actual) != digest:
            raise SyncError("snapshot verification failed")
        conn.execute("""INSERT INTO serialzone_sync_state VALUES (1,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET observed_at=excluded.observed_at,
            digest=excluded.digest, counts_json=excluded.counts_json""",
            (observed_at, digest, json.dumps(counts, sort_keys=True)))
        conn.commit()
        return changed
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("SERIALZONE_PROFILE_URL"))
    parser.add_argument("--db", default=os.environ.get("MOVIE_DB_PATH"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--initialize", action="store_true")
    parser.add_argument("--backup-dir")
    args = parser.parse_args()
    try:
        base = profile_url(args.profile or "")
        if (args.apply or args.initialize) and not args.db:
            raise SyncError("database path required")
        if args.initialize and not args.backup_dir:
            raise SyncError("private backup directory required")
        observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        rows, counts = collect(base, Fetcher())
        changed = None
        if args.apply or args.initialize:
            changed = apply_snapshot(args.db, rows, counts, observed_at, args.initialize, args.backup_dir)
        print(json.dumps({"status": "ok", "counts": counts, "unique_series": len({r[1] for r in rows}),
                          "changed": changed, "mode": "apply" if changed is not None else "collect-only"}))
        return 0
    except (SyncError, OSError, ValueError, sqlite3.Error) as exc:
        # No personal rows, profile URL, credentials or private paths in journals.
        print(json.dumps({"status": "failed", "reason": str(exc) if isinstance(exc, SyncError) else type(exc).__name__}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
