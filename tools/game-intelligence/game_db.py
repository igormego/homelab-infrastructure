#!/usr/bin/env python3
"""Read-only public Backloggd -> private local SQLite game catalog (no login)."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from html.parser import HTMLParser
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

PLAYED = {"played", "completed", "retired", "shelved", "abandoned"}
STATUS = PLAYED | {"playing", "wishlist", ""}
TABS = ("played", "playing", "backlog", "wishlist")
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  backloggd_game_id TEXT UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_games_title ON games(title COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS external_ids (
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  PRIMARY KEY(source, external_id),
  UNIQUE(game_id, source)
);
CREATE TABLE IF NOT EXISTS user_games (
  game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
  played_status TEXT,
  playing INTEGER NOT NULL DEFAULT 0 CHECK(playing IN (0,1)),
  backlog INTEGER NOT NULL DEFAULT 0 CHECK(backlog IN (0,1)),
  wishlist INTEGER NOT NULL DEFAULT 0 CHECK(wishlist IN (0,1)),
  rating REAL CHECK(rating IS NULL OR (rating >= 0 AND rating <= 5)),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  records INTEGER NOT NULL CHECK(records >= 0)
);
"""

@dataclass
class GameRow:
    title: str
    game_id: str | None = None
    played_status: str | None = None
    playing: bool = False
    backlog: bool = False
    wishlist: bool = False
    rating: float | None = None


def rating_value(value: str | None, *, ten_scale: bool = False) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError("Invalid rating") from None
    if not 0 <= result <= (10 if ten_scale else 5):
        raise ValueError("Rating out of range")
    return round(result / 2 if ten_scale else result, 2)


def read_csv(path: Path) -> list[GameRow]:
    """Read CSV snapshots, including optional stable IDs and overlapping status flags."""
    def yes_no(value: str | None, name: str) -> bool:
        normalized = (value or "").strip().lower()
        if normalized not in ("", "yes", "no", "true", "false", "1", "0"):
            raise ValueError(f"Invalid {name} value")
        return normalized in ("yes", "true", "1")

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"Title", "Rating", "Status", "Backlog"}.issubset(reader.fieldnames):
            raise ValueError("Expected Backloggd CSV columns: Title, Rating, Status, Backlog")
        rows = []
        for data in reader:
            title = (data["Title"] or "").strip()
            status = (data["Status"] or "").strip().lower()
            if not title or status not in STATUS:
                raise ValueError("Invalid title or status in CSV")
            game_id = (data.get("Backloggd ID") or "").strip()
            if game_id and not re.fullmatch(r"[1-9][0-9]{0,14}", game_id):
                raise ValueError("Invalid Backloggd game ID")
            rows.append(GameRow(
                title=title,
                game_id=game_id or None,
                played_status=status if status in PLAYED else None,
                playing=status == "playing" or yes_no(data.get("Playing"), "Playing"),
                wishlist=status == "wishlist" or yes_no(data.get("Wishlist"), "Wishlist"),
                backlog=yes_no(data["Backlog"], "Backlog"),
                rating=rating_value(data["Rating"]),
            ))
    return rows

class GameCardParser(HTMLParser):
    """Parse public game-cover cards only; ignore placeholders and private state."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: list[tuple[str, str | None, float | None, str | None]] = []
        self.level = 0
        self.item: dict | None = None

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "div" and self.item is not None:
            self.level += 1
        elif tag == "div" and "game-cover" in data.get("class", "").split():
            self.item = {"title": None, "game_id": data.get("game_id"),
                         "rating": rating_value(data.get("data-rating"), ten_scale=True),
                         "status": data.get("data-status-title")}
            self.level = 1
        if self.item is not None:
            if data.get("data-status-title"):
                self.item["status"] = data["data-status-title"]
            if tag == "img" and data.get("alt") and self.item["title"] is None:
                self.item["title"] = data["alt"].strip()

    def handle_endtag(self, tag):
        if tag == "div" and self.item is not None:
            self.level -= 1
            if self.level == 0:
                if self.item["title"]:
                    self.cards.append((str(self.item["title"]), self.item["game_id"],
                                       self.item["rating"], self.item["status"]))
                self.item = None


def parse_tab(html: str, tab: str) -> list[GameRow]:
    parser = GameCardParser()
    parser.feed(html)
    rows = []
    for title, game_id, rating, substatus in parser.cards:
        sub = (substatus or "").strip().lower()
        rows.append(GameRow(title, game_id=game_id, rating=rating,
                            played_status=(sub if sub in PLAYED else "played") if tab == "played" else None,
                            playing=tab == "playing", wishlist=tab == "wishlist", backlog=tab == "backlog"))
    return rows


def merge_rows(rows: list[GameRow]) -> list[GameRow]:
    combined: dict[str, GameRow] = {}
    titles: dict[str, str] = {}
    for row in rows:
        if not row.title.strip():
            raise ValueError("Missing game title")
        key = f"id:{row.game_id}" if row.game_id else f"title:{row.title.casefold()}"
        if key not in combined:
            combined[key] = GameRow(row.title, row.game_id)
        existing = combined[key]
        if row.game_id and existing.title != row.title:
            raise ValueError("Conflicting Backloggd game identity")
        title_key = row.title.casefold()
        if title_key in titles and titles[title_key] != key:
            previous = combined[titles[title_key]]
            # Distinct game editions can share titles when both have stable IDs.
            if not previous.game_id or not row.game_id:
                raise ValueError("Ambiguous same-title games: stable IDs required")
        titles[title_key] = key
        existing.played_status = row.played_status or existing.played_status
        existing.playing |= row.playing
        existing.backlog |= row.backlog
        existing.wishlist |= row.wishlist
        if row.rating is not None:
            if existing.rating is not None and row.rating != existing.rating:
                raise ValueError("Conflicting ratings")
            existing.rating = row.rating
    return list(combined.values())


def fetch_public_html(url: str) -> str:
    """Bounded unauthenticated GET. Do not send session cookies."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; personal-game-catalog/0.1)"})
    with urlopen(request, timeout=15) as response:
        html = response.read(2_500_001)
        if len(html) > 2_500_000:
            raise ValueError("Backloggd response too large")
        return html.decode("utf-8")


def sync_public(username: str, max_pages: int = 25, interval: float = 1.0,
                fetch_html: Callable[[str], str] = fetch_public_html) -> list[GameRow]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", username):
        raise ValueError("Invalid username")
    if not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be 1..100")
    all_rows: list[GameRow] = []
    for tab in TABS:
        seen_page_ids: set[tuple[str, ...]] = set()
        for page in range(1, max_pages + 1):
            url = f"https://backloggd.com/u/{username}/games/{tab}/?page={page}"
            try:
                html = fetch_html(url)
            except Exception as exc:
                raise RuntimeError("Public Backloggd fetch failed; no data imported") from exc
            if 'id="user_games"' not in html and "id='user_games'" not in html:
                raise RuntimeError("Unexpected Backloggd page (possibly anti-bot); no data imported")
            page_rows = parse_tab(html, tab)
            if not page_rows:
                break
            fingerprint = tuple(row.game_id or row.title for row in page_rows)
            if fingerprint in seen_page_ids:
                raise RuntimeError("Repeated Backloggd page; refusing incomplete import")
            seen_page_ids.add(fingerprint)
            all_rows.extend(page_rows)
            if len(page_rows) < 40:  # Observed page size; not a guaranteed API contract.
                break
            if page == max_pages:
                raise RuntimeError("Pagination cap reached; refusing incomplete import")
            if interval:
                time.sleep(interval)
    if not all_rows:
        raise RuntimeError("No public game cards; refusing empty snapshot")
    return merge_rows(all_rows)


def import_rows(db_path: Path, rows: list[GameRow], source: str) -> int:
    if not rows:
        raise ValueError("Empty import is not accepted")
    rows = merge_rows(rows)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.executescript(SCHEMA)
        with con:
            for row in rows:
                existing = None
                if row.game_id:
                    existing = con.execute("SELECT id FROM games WHERE backloggd_game_id=?", (row.game_id,)).fetchone()
                if existing is None:
                    if row.game_id:
                        match = con.execute("SELECT id, backloggd_game_id FROM games WHERE title=? COLLATE NOCASE AND backloggd_game_id IS NULL", (row.title,)).fetchall()
                    else:
                        match = con.execute("SELECT id, backloggd_game_id FROM games WHERE title=? COLLATE NOCASE", (row.title,)).fetchall()
                    if len(match) > 1:
                        raise ValueError("Ambiguous identity; import aborted")
                    existing = match[0] if match else None
                if existing is None:
                    cur = con.execute("INSERT INTO games(title,backloggd_game_id) VALUES (?,?)", (row.title,row.game_id))
                    pk = cur.lastrowid
                else:
                    pk = existing[0]
                    con.execute("UPDATE games SET title=?,backloggd_game_id=COALESCE(?,backloggd_game_id) WHERE id=?", (row.title,row.game_id,pk))
                if row.game_id:
                    con.execute("INSERT OR IGNORE INTO external_ids(game_id,source,external_id) VALUES (?,'backloggd',?)", (pk,row.game_id))
                con.execute("""INSERT INTO user_games(game_id,played_status,playing,backlog,wishlist,rating)
                               VALUES(?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET
                               played_status=excluded.played_status,playing=excluded.playing,
                               backlog=excluded.backlog,wishlist=excluded.wishlist,rating=excluded.rating,
                               updated_at=CURRENT_TIMESTAMP""",
                            (pk,row.played_status,int(row.playing),int(row.backlog),int(row.wishlist),row.rating))
            con.execute("INSERT INTO sync_runs(source, records) VALUES (?,?)", (source,len(rows)))
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Backloggd public profile -> SQLite importer")
    ap.add_argument("--db", required=True, type=Path, help="Private local SQLite path")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv", type=Path, help="Optional fallback CSV")
    group.add_argument("--public-profile", help="Read public profile without signing in")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing SQLite")
    args = ap.parse_args()
    rows = read_csv(args.csv) if args.csv else sync_public(args.public_profile, max_pages=args.max_pages)
    if args.dry_run:
        print(f"Validated {len(merge_rows(rows))} distinct games; database unchanged")
        return
    count = import_rows(args.db, rows, "backloggd_csv" if args.csv else "backloggd_public")
    print(f"Imported {count} distinct games into {args.db}")

if __name__ == "__main__":
    main()
