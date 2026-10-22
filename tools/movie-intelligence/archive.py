"""Single-movie historical archive. No acquisition, watched writes or lifecycle deletion."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request


class Blocked(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked('redirect_refused')


class ReadAPI:
    def __init__(self, base, key, header='X-Api-Key'):
        u = urllib.parse.urlsplit(base)
        require(u.scheme in ('http', 'https') and u.hostname and not u.username and
                not u.password and not u.query and not u.fragment and bool(key), 'invalid_api_config')
        self.base, self.key, self.header = base.rstrip('/'), key, header

    def bytes(self, path):
        require(path.startswith('/') and not path.startswith('//'), 'invalid_api_path')
        try:
            req = urllib.request.Request(self.base + path, headers={self.header: self.key, 'Accept': 'application/json'})
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as r:
                data = r.read(8 * 1024 * 1024 + 1)
                require(len(data) <= 8 * 1024 * 1024 and self.key.encode() not in data, 'invalid_api_response')
                return data
        except Exception:
            raise Blocked('api_unavailable') from None

    def get(self, path):
        try:
            return json.loads(self.bytes(path))
        except Exception:
            raise Blocked('api_unavailable') from None


def watched_identity(db_path, tmdb):
    """Read one identity from the accepted normalized schema, never historical dates."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('''SELECT DISTINCT m.id, m.title, m.year, u.watched
          FROM movies m LEFT JOIN user_movies u ON u.movie_id=m.id
          JOIN external_ids e ON e.movie_id=m.id
          WHERE e.source='tmdb' AND CAST(e.external_id AS INTEGER)=?''', (tmdb,)).fetchall()
        require(len(rows) == 1 and rows[0]['watched'] == 1, 'historical_watched_not_unique_or_true')
        m = dict(rows[0]); ids = db.execute('SELECT source,external_id FROM external_ids WHERE movie_id=?', (m['id'],)).fetchall()
        canonical = {}
        for row in ids:
            if row['source'] in ('tmdb', 'imdb'):
                require(row['source'] not in canonical, 'duplicate_identity')
                canonical[row['source']] = row['external_id']
        require(re.fullmatch(r'[0-9]+', canonical.get('tmdb', '')) and int(canonical['tmdb']) == tmdb,
                'invalid_tmdb_identity')
        imdb = canonical.get('imdb')
        require(isinstance(imdb, str) and re.fullmatch(r'tt[0-9]{7,10}', imdb), 'imdb_identity_required')
        owners = db.execute("SELECT DISTINCT movie_id FROM external_ids WHERE source='imdb' AND external_id=?", (imdb,)).fetchall()
        require(len(owners) == 1 and owners[0][0] == m['id'], 'ambiguous_imdb')
        return {'tmdb': tmdb, 'imdb': imdb, 'year': m['year'], 'watched': True}
    finally:
        db.close()


def canonical_ids(item):
    ids = item.get('Guid', [])
    require(isinstance(ids, list), 'unknown_plex_identity')
    result = {}
    for g in ids:
        value = g.get('id', '')
        for source, pattern in [('tmdb', r'tmdb://([0-9]+)'), ('imdb', r'imdb://(tt[0-9]{7,10})')]:
            match = re.fullmatch(pattern, value)
            if match:
                require(source not in result, 'duplicate_plex_identity')
                result[source] = int(match[1]) if source == 'tmdb' else match[1]
    require(result, 'unknown_plex_identity')
    return result


def matching(rows, identity):
    matches = []
    for row in rows:
        ids = canonical_ids(row)
        if ids.get('tmdb') == identity['tmdb'] or ids.get('imdb') == identity['imdb']:
            require(all(ids[k] == identity[k] for k in ids), 'plex_identity_conflict')
            matches.append(row)
    require(len(matches) <= 1, 'duplicate_plex_movie')
    return matches


class Archive:
    def __init__(self, config, plex, radarr, seerr, identity_reader, renderer):
        self.c, self.plex, self.radarr, self.seerr = config, plex, radarr, seerr
        self.identity_reader, self.renderer = identity_reader, renderer
        self.root, self.state = Path(config['host_root']), Path(config['state_root'])
        for p in (self.root, self.state):
            require(p.is_absolute() and p.is_dir() and not p.is_symlink() and p.resolve() == p, 'unsafe_directory')
        require(not overlap(self.root, self.state) and self.state.stat().st_mode & 0o077 == 0, 'unsafe_state_directory')
        require(self.root.stat().st_dev == self.state.stat().st_dev, 'publication_requires_same_filesystem')
        require(config['forbidden_host_roots'] and all(not overlap(self.root, Path(p).resolve()) for p in config['forbidden_host_roots']), 'archive_not_isolated')
        require(config['archive_id'] != config['movies_id'], 'archive_is_main_library')

    def listing(self, library):
        rows, total = [], None
        for start in range(0, 100000, 100):
            c = self.plex.get(f'/library/sections/{library}/all?type=1&includeGuids=1&X-Plex-Container-Start={start}&X-Plex-Container-Size=100')['MediaContainer']
            batch = c.get('Metadata', [])
            require(isinstance(batch, list) and len(batch) <= 100 and c.get('offset', 0) == start and
                    type(c.get('totalSize')) is int and 0 <= c['totalSize'] <= 100000 and
                    (total is None or total == c['totalSize']) and c.get('size') == len(batch), 'incomplete_plex_listing')
            total = c['totalSize']; rows.extend(batch)
            if len(rows) == total:
                require(len({x['ratingKey'] for x in rows}) == total, 'duplicate_listing')
                return rows
            require(len(batch) == 100 and len(rows) < total, 'incomplete_plex_listing')
        raise Blocked('listing_limit')

    def detail(self, key):
        require(re.fullmatch(r'[0-9]+', str(key)), 'invalid_rating_key')
        rows = self.plex.get(f'/library/metadata/{key}?includeGuids=1&checkFiles=1')['MediaContainer']['Metadata']
        require(len(rows) == 1 and str(rows[0]['ratingKey']) == str(key), 'invalid_metadata')
        return rows[0]

    def receipt_path(self, tmdb):
        return self.state / f'{tmdb}.json'

    def receipt(self, tmdb):
        p = self.receipt_path(tmdb)
        if not p.exists():
            return None
        require(p.is_file() and not p.is_symlink() and p.stat().st_size < 65536, 'unsafe_receipt')
        return json.loads(p.read_text())

    def inspect(self, tmdb):
        require(type(tmdb) is int and tmdb > 0, 'tmdb_required')
        identity = self.identity_reader(tmdb)
        require(identity['tmdb'] == tmdb and identity['watched'] is True, 'not_historically_watched')
        meta = self.seerr.get(f'/api/v1/movie/{tmdb}')
        require(meta['id'] == tmdb and meta.get('imdbId') == identity['imdb'] and
                int(meta['releaseDate'][:4]) == identity['year'], 'canonical_identity_conflict')
        title = meta['title']; require(isinstance(title, str) and 0 < len(title) < 200, 'invalid_title')
        identity = {**identity, 'title': title}
        info = meta.get('mediaInfo') or {}
        require(not info.get('requests') and info.get('status', 1) == 1 and info.get('status4k', 1) == 1, 'seerr_conflict')
        libraries = self.seerr.get('/api/v1/settings/plex')['libraries']
        require(isinstance(libraries, list) and not any(str(x['id']) == str(self.c['archive_id']) and x.get('enabled') is not False for x in libraries), 'archive_sync_not_excluded')
        sections = self.plex.get('/library/sections')['MediaContainer']['Directory']
        archive = [s for s in sections if str(s['key']) == str(self.c['archive_id'])]
        require(len(archive) == 1 and archive[0]['title'] == self.c['archive_name'] and archive[0]['type'] == 'movie' and
                [x['path'] for x in archive[0]['Location']] == [self.c['plex_root']], 'archive_scope_changed')
        main = [s for s in sections if str(s['key']) == str(self.c['movies_id'])]
        require(len(main) == 1 and main[0]['type'] == 'movie' and main[0]['title'] == 'Movies', 'main_scope_changed')
        require(all(not overlap(PurePosixPath(self.c['plex_root']), PurePosixPath(loc['path']))
                    for s in sections if s is not archive[0] for loc in s.get('Location', [])), 'library_root_overlap')
        movies = self.radarr.get('/api/v3/movie')
        require(isinstance(movies, list), 'radarr_unavailable')
        managed = [m for m in movies if m.get('tmdbId') == tmdb or m.get('imdbId') == identity['imdb']]
        require(len(managed) <= 1, 'ambiguous_radarr')
        if managed:
            m = managed[0]
            require(m['tmdbId'] == tmdb and (not m.get('imdbId') or m['imdbId'] == identity['imdb']) and
                    m['hasFile'] is False and not m.get('movieFileId') and not m.get('sizeOnDisk'), 'radarr_file_or_identity_conflict')
            if tmdb in self.c.get('expected_radarr_ids', {}):
                require(m['id'] == self.c['expected_radarr_ids'][tmdb] and m['monitored'] is False, 'pilot_radarr_state_changed')
            q = self.radarr.get('/api/v3/queue?page=1&pageSize=100&includeUnknownMovieItems=true')
            require(q.get('totalRecords') == len(q.get('records', [])) and q.get('page') == 1, 'queue_not_complete')
            require(not any(not r.get('movieId') or r['movieId'] == m['id'] for r in q['records']), 'queue_conflict')
        elif tmdb in self.c.get('expected_radarr_ids', {}):
            raise Blocked('pilot_radarr_missing')
        real = matching(self.listing(self.c['movies_id']), identity)
        if real:
            d = self.detail(real[0]['ratingKey'])
            require(canonical_ids(d) == canonical_ids(real[0]), 'main_identity_changed')
            parts = [p for med in d.get('Media', []) for p in med.get('Part', [])]
            require(parts and all(p.get('exists') is False for p in parts), 'playable_or_unknown_main_movie')
        existing = matching(self.listing(self.c['archive_id']), identity)
        safe = re.sub(r'[^\w .()-]+', '', title, flags=re.UNICODE).strip(' .')
        require(safe and safe not in ('.', '..'), 'invalid_filename')
        folder = f'{safe} ({identity["year"]}) {{tmdb-{tmdb}}}'
        path = self.root / folder / (folder + '.mp4')
        require(path.parent.parent == self.root and not path.parent.is_symlink() and not path.is_symlink(), 'unsafe_target_path')
        receipt = self.receipt(tmdb)
        if receipt:
            require(receipt['identity'] == identity and receipt['file'] == str(path), 'receipt_identity_changed')
        elif existing or path.parent.exists():
            raise Blocked('unowned_archive_conflict')
        guard = {'identity': identity, 'radarr': managed, 'root': self.c['plex_root'], 'library': self.c['archive_id'], 'file': str(path)}
        return guard, existing, receipt

    def verify(self, guard, existing, receipt):
        require(receipt and receipt.get('sha256') and len(existing) == 1, 'indexing_pending')
        p = Path(guard['file']); require(p.is_file() and not p.is_symlink() and p.stat().st_size <= 2 * 1024 * 1024 and
                                       hashlib.sha256(p.read_bytes()).hexdigest() == receipt['sha256'], 'placeholder_changed')
        d = self.detail(existing[0]['ratingKey']); ids = canonical_ids(d)
        require(ids.get('tmdb') == guard['identity']['tmdb'] and ids.get('imdb') == guard['identity']['imdb'] and
                d['year'] == guard['identity']['year'] and str(d['librarySectionID']) == str(self.c['archive_id']) and
                d.get('title') == guard['identity']['title'], 'indexed_identity_mismatch')
        parts = [p for m in d.get('Media', []) for p in m.get('Part', [])]
        expected = self.c['plex_root'] + '/' + str(Path(guard['file']).relative_to(self.root)).replace(os.sep, '/')
        require(len(parts) == 1 and parts[0]['file'] == expected and parts[0].get('exists') is True and
                parts[0].get('accessible') is True, 'indexed_file_mismatch')
        require(not d.get('viewCount') and not d.get('lastViewedAt'), 'unexpected_playback_history')
        thumb = d.get('thumb', '')
        require(thumb.startswith(f'/library/metadata/{d["ratingKey"]}/thumb/'), 'poster_missing')
        require(len(self.plex.bytes(thumb)) > 256, 'poster_unavailable')
        return {'rating_key': d['ratingKey'], 'guids': ids, 'year': d['year'], 'poster': 'verified', 'file_bytes': Path(guard['file']).stat().st_size}

    def plan_archive_movie(self, tmdb):
        try:
            guard, existing, receipt = self.inspect(tmdb)
            if receipt:
                verified = self.verify(guard, existing, receipt)
                return {'decision': 'already_archived', 'mutation_attempted': False, 'verified': verified}
            return {'decision': 'eligible', 'reason': 'historical_watched_no_playable_copy', 'mutation_attempted': False,
                    'identity': guard['identity'], 'file': guard['file'], 'plan_fingerprint': digest(guard),
                    'operations': ['create one synthetic MP4', f'scan only library {self.c["archive_id"]} target folder', 'verify canonical metadata/poster'],
                    'preserve': ['Plex watched/history', 'Movies', 'Radarr', 'Seerr', 'media.db', 'Trakt', 'existing archive items']}
        except Blocked as e:
            return {'decision': 'blocked', 'reason': str(e), 'mutation_attempted': False}
        except Exception:
            return {'decision': 'blocked', 'reason': 'source_or_receipt_unavailable', 'mutation_attempted': False}

    def save(self, tmdb, value, exclusive=False):
        p = self.receipt_path(tmdb)
        target = p if exclusive else p.with_suffix('.next')
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2); f.flush(); os.fsync(f.fileno())
        if not exclusive:
            os.replace(target, p)
        fd = os.open(self.state, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def archive_movie(self, tmdb, *, execute=False, plan_fingerprint=None):
        if type(execute) is not bool:
            return {'decision': 'blocked', 'reason': 'explicit_boolean_required', 'mutation_attempted': False}
        if not execute:
            return self.plan_archive_movie(tmdb)
        attempted = False
        try:
            require(tmdb in self.c.get('execution_allowlist', []), 'execution_not_authorized_for_identity')
            guard, existing, receipt = self.inspect(tmdb)
            if receipt:
                return {'decision': 'already_archived', 'mutation_attempted': False, 'verified': self.verify(guard, existing, receipt)}
            require(plan_fingerprint == digest(guard), 'reviewed_plan_changed_or_missing')
            receipt = {'version': 1, 'identity': guard['identity'], 'file': guard['file'], 'library': self.c['archive_id'],
                       'stage': 'reserved', 'created_directory': str(Path(guard['file']).parent),
                       'private_payload': str(self.state / f'{tmdb}.mp4'), 'created_at': time.time()}
            self.save(tmdb, receipt, exclusive=True)  # durable cross-process reservation, never auto-expired
            fresh, existing, _ = self.inspect(tmdb)
            require(fresh == guard and not existing, 'reserved_preflight_changed')
            attempted = True
            data = self.renderer(guard['identity'])
            require(1024 < len(data) <= 2 * 1024 * 1024 and data[4:8] == b'ftyp', 'invalid_synthetic_video')
            payload = Path(receipt['private_payload'])
            fd = os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            fresh, existing, _ = self.inspect(tmdb)  # revalidate immediately before publishing
            require(fresh == guard and not existing, 'prepublish_eligibility_changed')
            Path(guard['file']).parent.mkdir(mode=0o755)
            os.link(payload, guard['file'])  # atomic publication, refuses any existing destination
            receipt.update(stage='file_created', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            self.save(tmdb, receipt)
            receipt['stage'] = 'scan_attempt'; self.save(tmdb, receipt)
            folder = self.c['plex_root'] + '/' + Path(guard['file']).parent.name
            self.plex.bytes(f'/library/sections/{self.c["archive_id"]}/refresh?' + urllib.parse.urlencode({'path': folder}))
            for _ in range(self.c.get('poll_attempts', 12)):
                try:
                    existing = matching(self.listing(self.c['archive_id']), guard['identity'])
                    if existing:
                        verified = self.verify(guard, existing, receipt)
                        receipt.update(stage='verified', verified=verified); self.save(tmdb, receipt)
                        return {'decision': 'archived', 'mutation_attempted': True, 'verified': verified, 'file': guard['file']}
                except Blocked as e:
                    if str(e) not in ('unknown_plex_identity', 'indexed_identity_mismatch', 'poster_missing', 'poster_unavailable'):
                        raise
                time.sleep(self.c.get('poll_interval', 2))
            raise Blocked('indexing_pending_no_rescan')
        except Blocked as e:
            return {'decision': 'needs_review' if attempted else 'blocked', 'reason': str(e), 'mutation_attempted': attempted}
        except Exception:
            return {'decision': 'needs_review' if attempted else 'blocked', 'reason': 'operation_or_receipt_uncertain_no_retry', 'mutation_attempted': attempted}


RENDER_IMAGE = 'lscr.io/linuxserver/bazarr@sha256:762f802274598da27255b2e5778f262b2b71b355a23e3812d9ee1520f8dbe37c'
RENDER_FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
RENDER_FONT_SHA256 = 'b4c632e3cdf9acc7f28758fb5a323c8524d7fc6660d46904d9b6cbe2809c419c'


def render_synthetic(identity, *, fontfile=RENDER_FONT, font_sha256=RENDER_FONT_SHA256):
    """Pinned, networkless one-shot tools; never exec into or modify a service."""
    font = Path(fontfile)
    require(font.is_absolute() and font.is_file() and not font.is_symlink() and
            font.resolve() == font and ',' not in str(font), 'invalid_renderer_font')
    require(hashlib.sha256(font.read_bytes()).hexdigest() == font_sha256, 'renderer_font_hash_mismatch')
    base = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
            '--memory=256m', '--cpus=1', '--user', f'{os.getuid()}:{os.getgid()}']
    label = unicodedata.normalize('NFKD', identity['title']).encode('ascii', 'ignore').decode()
    label = re.sub('[^A-Za-z0-9 ]', '', label).upper()[:45] + ' ' + str(identity['year'])
    filters = ','.join(f"drawtext=fontfile=/archive-font.ttf:text='{text}':fontcolor=white:fontsize={size}:x=(w-text_w)/2:y={y}"
                       for text, size, y in [('ARCHIVE PLACEHOLDER', 30, 90), ('NOT THE ACTUAL FILM', 24, 145), (label, 20, 210)])
    cmd = base + ['--mount', f'type=bind,source={font},target=/archive-font.ttf,readonly',
           '--entrypoint', '/usr/bin/ffmpeg', RENDER_IMAGE, '-hide_banner', '-loglevel', 'error',
           '-f', 'lavfi', '-i', 'color=c=0x17212b:s=640x360:r=24', '-vf', filters, '-t', '60', '-an',
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '35', '-pix_fmt', 'yuv420p',
           '-movflags', '+faststart', '-f', 'mp4', '/render/placeholder.mp4']
    # A seekable file produces an ordinary MP4 with complete duration/index metadata.
    # Streaming fragmented MP4 probing can report only the first fragment duration.
    with tempfile.TemporaryDirectory(prefix='archive-render-') as directory:
        output = Path(directory) / 'placeholder.mp4'
        mount = f'type=bind,source={directory},target=/render'
        cmd[2:2] = ['--mount', mount]
        r = subprocess.run(cmd, capture_output=True, timeout=45)
        require(r.returncode == 0 and output.is_file() and not output.is_symlink() and
                1024 < output.stat().st_size <= 2 * 1024 * 1024, 'encoder_failed')
        probe = subprocess.run(base + ['--mount', mount + ',readonly', '--entrypoint',
                               '/usr/bin/ffprobe', RENDER_IMAGE, '-v', 'error', '-show_streams',
                               '-show_format', '-of', 'json', '/render/placeholder.mp4'],
                               capture_output=True, timeout=15)
        data = output.read_bytes()
    require(probe.returncode == 0, 'synthetic_probe_failed')
    info = json.loads(probe.stdout); streams = info['streams']
    require(len(streams) == 1 and streams[0]['codec_name'] == 'h264' and streams[0]['pix_fmt'] == 'yuv420p' and
            streams[0].get('width') == 640 and streams[0].get('height') == 360 and
            59 <= float(info['format']['duration']) <= 61, 'synthetic_format_invalid')
    return data
