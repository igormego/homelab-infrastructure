"""Read-only My Cinema inventory. Never creates/deletes media or starts searches.

Run privately on the Homelab with existing credential environment. This tool
does not authorize writes and cannot produce Plex/ARR/Seerr mutations.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from archive import ReadAPI
from lifecycle import plan_lifecycle

MAX_RECORDS = 10000


def watched_catalog(db_path):
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("""
          SELECT m.id,m.title,m.year,u.watched,e.source,e.external_id
          FROM movies m JOIN user_movies u ON u.movie_id=m.id
          LEFT JOIN external_ids e ON e.movie_id=m.id
          WHERE u.watched=1 ORDER BY m.id,e.source
        """).fetchall()
        if len(rows) > MAX_RECORDS * 4:
            raise ValueError("catalog_too_large")
        found = {}
        owners = {}
        for movie_id, title, year, watched, source, external_id in rows:
            if movie_id not in found:
                if len(found) >= MAX_RECORDS: raise ValueError("catalog_too_large")
                found[movie_id] = {"movie_id":movie_id,"title":title,"year":year,"watched":watched==1}
            if source in ("tmdb","imdb"):
                if not isinstance(external_id,str): raise ValueError("invalid_external_id")
                ident = int(external_id) if source=="tmdb" and re.fullmatch(r"[1-9][0-9]*",external_id) else external_id
                if source=="imdb" and not re.fullmatch(r"tt[0-9]{7,10}",external_id):
                    raise ValueError("invalid_imdb")
                if source=="tmdb" and (not isinstance(ident,int) or ident<=0):
                    raise ValueError("invalid_tmdb")
                key=(source,ident)
                if key in owners and owners[key]!=movie_id: raise ValueError("ambiguous_external_id")
                owners[key]=movie_id
                if source in found[movie_id]: raise ValueError("duplicate_external_id")
                found[movie_id][source]=ident
        return list(found.values())
    finally:
        db.close()


def plex_listing(client, library):
    rows = [];total=None
    for start in range(0,MAX_RECORDS,100):
        c=client.get(f"/library/sections/{library}/all?type=1&includeGuids=1&X-Plex-Container-Start={start}&X-Plex-Container-Size=100")["MediaContainer"]
        batch=c.get("Metadata",[])
        if not isinstance(batch,list) or len(batch)>100 or type(c.get("totalSize")) is not int or (
                total is not None and total!=c["totalSize"]) or c.get("offset",0)!=start or c.get("size")!=len(batch):
            raise ValueError("plex_listing_incomplete")
        total=c["totalSize"]
        if total>MAX_RECORDS:raise ValueError("plex_listing_too_large")
        rows.extend(batch)
        if len(rows)==total:
            if len({x["ratingKey"] for x in rows})!=total:raise ValueError("plex_duplicate_key")
            break
        if len(rows)>total or len(batch)!=100:raise ValueError("plex_listing_incomplete")
    if len(rows)!=total: raise ValueError("plex_listing_incomplete")
    # Verify actual on-disk file availability for each item, not only Plex metadata.
    details=[]
    for row in rows:
        key=str(row["ratingKey"])
        if not key.isdecimal():raise ValueError("plex_invalid_key")
        d=client.get(f"/library/metadata/{key}?checkFiles=1&includeGuids=1")["MediaContainer"]["Metadata"]
        if len(d)!=1 or str(d[0].get("ratingKey"))!=key:raise ValueError("plex_detail_incomplete")
        details.append(d[0])
    return details


def snapshot(*, db_path, plex, radarr, movies_id, archive_id):
    if movies_id==archive_id or min(movies_id,archive_id)<1:
        raise ValueError("invalid_library_scope")
    sections=plex.get("/library/sections")["MediaContainer"]["Directory"]
    main=[s for s in sections if str(s["key"])==str(movies_id)]
    arc=[s for s in sections if str(s["key"])==str(archive_id)]
    if len(main)!=1 or main[0].get("title")!="Movies" or main[0].get("type")!="movie" or (
            len(arc)!=1 or not arc[0].get("title","").startswith("Watched") or arc[0].get("type")!="movie"):
        raise ValueError("unexpected_library_scope")
    radarr_movies=radarr.get("/api/v3/movie")
    if not isinstance(radarr_movies,list) or len(radarr_movies)>MAX_RECORDS:
        raise ValueError("radarr_listing_incomplete")
    return (watched_catalog(db_path),radarr_movies,
            plex_listing(plex,movies_id),plex_listing(plex,archive_id))



def archived_receipts(directory):
    """Read only complete, hash-verified private archive receipts, never adopt foreign files."""
    if not directory:
        return {}
    root=Path(directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("unsafe_receipt_directory")
    receipts={}
    for p in root.glob("*.json"):
        if not p.stem.isdecimal() or p.is_symlink() or not p.is_file() or p.stat().st_size>65536:
            raise ValueError("unsafe_receipt_file")
        if p.stat().st_mode & 0o077:
            raise ValueError("unsafe_receipt_permissions")
        tmdb=int(p.stem)
        if tmdb in receipts or len(receipts)>MAX_RECORDS:
            raise ValueError("duplicate_archive_receipt")
        data=json.loads(p.read_text())
        if data.get("stage") != "verified" or (data.get("identity") or {}).get("tmdb") != tmdb:
            continue
        target=Path(data.get("file",""))
        if not target.is_absolute() or target.is_symlink() or not target.is_file() or (
                target.stat().st_size>2*1024*1024):
            continue
        if hashlib.sha256(target.read_bytes()).hexdigest()!=data.get("sha256"):
            continue
        if not isinstance((data.get("verified") or {}).get("rating_key"), (str,int)):
            continue
        receipts[tmdb]=data
    return receipts


def build_report(catalog,radarr_movies,movies,archive,receipts=None,limit=30):
    # receipts are independently validated archive receipts supplied by the
    # private owner workflow. Absent receipts deliberately trigger needs_review
    # for existing archive entries; no silent ownership adoption.
    receipts=receipts or {}
    if not isinstance(receipts,dict):raise ValueError("invalid_receipts")
    results=[]
    for movie in catalog:
        canonical={"tmdb":movie.get("tmdb"),"imdb":movie.get("imdb"),"watched":movie["watched"]}
        plan=plan_lifecycle(canonical,radarr_movies,movies,archive,receipts.get(movie.get("tmdb")))
        results.append({"tmdb":movie.get("tmdb"),"title":movie["title"],
                        "year":movie["year"],"decision":plan["decision"],
                        "action":plan["action"],"reason":plan["reason"]})
    counts=Counter(p["decision"] for p in results)
    return {"read_only":True,"count":len(results),
            "decisions":dict(sorted(counts.items())),"items":results[:limit],
            "truncated":len(results)>limit}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--limit",type=int,default=30)
    args=ap.parse_args()
    if not 0<=args.limit<=100: raise ValueError("invalid_preview_limit")
    need=lambda k:os.environ[k]
    plex=ReadAPI(need("PLEX_BASE_URL"),need("PLEX_TOKEN"),"X-Plex-Token")
    radarr=ReadAPI(need("RADARR_BASE_URL"),need("RADARR_API_KEY"))
    data=snapshot(db_path=need("MOVIE_DB_PATH"),plex=plex,radarr=radarr,
                  movies_id=int(need("PLEX_MOVIES_LIBRARY_ID")),
                  archive_id=int(need("PLEX_ARCHIVE_LIBRARY_ID")))
    receipts=archived_receipts(os.environ.get('MY_CINEMA_ARCHIVE_STATE_DIR'))
    print(json.dumps(build_report(*data,receipts=receipts,limit=args.limit),ensure_ascii=False))


if __name__=="__main__":
    try: main()
    except Exception:
        # Fail closed, never print upstream URLs, tokens, database paths or raw errors.
        print(json.dumps({"read_only":True,"status":"blocked","reason":"source_or_scope_unavailable"}))
        sys.exit(2)
