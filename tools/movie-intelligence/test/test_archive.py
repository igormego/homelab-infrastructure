import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from archive import Archive, Blocked, ReadAPI, watched_identity, render_synthetic


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        for name in ('library', 'state', 'movies'):
            (self.root / name).mkdir(mode=0o700)
        self.identity = {'tmdb': 123, 'imdb': 'tt1234567', 'year': 2024, 'watched': True}
        self.config = {'host_root': str(self.root / 'library'), 'state_root': str(self.root / 'state'),
                       'forbidden_host_roots': [str(self.root / 'movies')], 'archive_id': 4, 'movies_id': 1,
                       'archive_name': 'Watched — Test', 'plex_root': '/archive', 'execution_allowlist': [123],
                       'expected_radarr_ids': {123: 208}, 'poll_attempts': 2, 'poll_interval': 0}
        self.sections = [{'key': '1', 'title': 'Movies', 'type': 'movie', 'Location': [{'path': '/movies'}]},
                         {'key': '4', 'title': 'Watched — Test', 'type': 'movie', 'Location': [{'path': '/archive'}]}]
        self.managed = [{'id': 208, 'tmdbId': 123, 'imdbId': 'tt1234567', 'hasFile': False, 'monitored': False,
                         'qualityProfileId': 9, 'rootFolderPath': '/movies', 'tags': []}]
        self.meta = {'id': 123, 'imdbId': 'tt1234567', 'releaseDate': '2024-01-01', 'title': 'Fixture Movie'}
        self.library_sync = [{'id': '1', 'enabled': True}, {'id': '4', 'enabled': False}]
        self.main, self.archive, self.queue = [], [], []
        self.calls, self.render_count, self.scan_count = [], 0, 0
        self.fail_scan, self.index, self.on_render = False, True, None
        self.bad_metadata = False
        self.controller = self.create()

    def create(self):
        return Archive(self.config, self, self, self, lambda _: copy.deepcopy(self.identity), self.render)

    def render(self, identity):
        self.render_count += 1
        if self.on_render:
            self.on_render()
        return b'\x00\x00\x00\x20ftyp' + b'x' * 4096

    def get(self, path):
        self.calls.append(path)
        if path == '/api/v1/movie/123':
            return copy.deepcopy(self.meta)
        if path == '/api/v1/settings/plex':
            return {'libraries': self.library_sync}
        if path == '/library/sections':
            return {'MediaContainer': {'Directory': self.sections}}
        if path == '/api/v3/movie':
            return copy.deepcopy(self.managed)
        if path.startswith('/api/v3/queue?'):
            return {'page': 1, 'totalRecords': len(self.queue), 'records': self.queue}
        for lib, rows in [(1, self.main), (4, self.archive)]:
            if path.startswith(f'/library/sections/{lib}/all?'):
                return {'MediaContainer': {'size': len(rows), 'totalSize': len(rows), 'offset': 0, 'Metadata': copy.deepcopy(rows)}}
        if path.startswith('/library/metadata/'):
            key = path.split('/')[3].split('?')[0]
            return {'MediaContainer': {'Metadata': copy.deepcopy([m for m in self.main + self.archive if m['ratingKey'] == key])}}
        raise AssertionError('Unexpected API path: ' + path)

    def bytes(self, path):
        self.calls.append(path)
        if path.startswith('/library/sections/4/refresh?'):
            self.scan_count += 1
            if self.fail_scan:
                raise Blocked('scan_response_uncertain')
            if self.index:
                folder = 'Fixture Movie (2024) {tmdb-123}'
                self.archive = [{'ratingKey': '500', 'librarySectionID': 4, 'title': 'Fixture Movie', 'year': 2024,
                                 'Guid': [{'id': 'tmdb://123'}, {'id': 'imdb://tt1234567'}],
                                 'thumb': '/library/metadata/500/thumb/1',
                                 'Media': [{'Part': [{'file': f'/archive/{folder}/{folder}.mp4', 'exists': True, 'accessible': True}]}]}]
                if self.bad_metadata:
                    self.archive[0]['year'] = 2023
            return b''
        if path == '/library/metadata/500/thumb/1':
            return b'poster' * 100
        raise AssertionError('Unexpected byte path: ' + path)

    def execute(self):
        p = self.controller.plan_archive_movie(123)
        return self.controller.archive_movie(123, execute=True, plan_fingerprint=p.get('plan_fingerprint'))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = Fixture(self.tmp.name)

    def test_dry_plan_no_files_scans_or_mutations(self):
        p = self.h.controller.archive_movie(123)
        self.assertEqual(p['decision'], 'eligible')
        self.assertFalse(p['mutation_attempted'])
        self.assertEqual(list((self.h.root / 'state').iterdir()), [])
        self.assertEqual(list((self.h.root / 'library').iterdir()), [])
        self.assertEqual(self.h.render_count, 0)

    def test_one_archive_and_safe_repeat_new_controller(self):
        before = copy.deepcopy(self.h.managed)
        r = self.h.execute()
        self.assertEqual(r['decision'], 'archived', r)
        self.assertEqual(r['verified']['poster'], 'verified')
        self.assertEqual(self.h.managed, before)
        self.h.controller = self.h.create()
        self.assertEqual(self.h.controller.archive_movie(123, execute=True)['decision'], 'already_archived')
        self.assertEqual(self.h.controller.archive_movie(123)['decision'], 'already_archived')
        self.assertEqual((self.h.render_count, self.h.scan_count), (1, 1))
        self.assertEqual(len(list((self.h.root / 'library').glob('*/*.mp4'))), 1)
        self.assertFalse(any('/scrobble' in x or '/api/v3/command' in x for x in self.h.calls))

    def test_watched_must_be_true(self):
        for value in [False, None, 0]:
            self.h.identity['watched'] = value
            self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.assertEqual(self.h.render_count, 0)

    def test_conflicting_canonical_identity(self):
        for key, value in [('imdbId', 'tt7654321'), ('id', 124), ('releaseDate', '2023-01-01')]:
            old = self.h.meta[key]; self.h.meta[key] = value
            self.assertEqual(self.h.execute()['decision'], 'blocked')
            self.h.meta[key] = old

    def test_radarr_file_and_ambiguity_block(self):
        self.h.managed[0]['hasFile'] = True
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.h.managed[0]['hasFile'] = False
        self.h.managed.append(copy.deepcopy(self.h.managed[0]))
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_pilot_radarr_monitoring_change_blocks(self):
        self.h.managed[0]['monitored'] = True
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_unmanaged_watched_movie_general_contract(self):
        self.h.config['expected_radarr_ids'] = {}; self.h.managed = []; self.h.controller = self.h.create()
        self.assertEqual(self.h.controller.plan_archive_movie(123)['decision'], 'eligible')

    def test_unknown_or_playable_main_item_blocks(self):
        self.h.main = [{'ratingKey': '10', 'Guid': []}]
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.h.main[0].update(Guid=[{'id': 'tmdb://123'}], Media=[{'Part': [{'exists': True}]}])
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.h.main[0]['Media'][0]['Part'][0]['exists'] = False
        self.assertEqual(self.h.controller.plan_archive_movie(123)['decision'], 'eligible')

    def test_existing_foreign_archive_item_or_folder_never_adopted(self):
        self.h.archive = [{'ratingKey': '10', 'Guid': [{'id': 'tmdb://123'}]}]
        self.assertEqual(self.h.execute()['reason'], 'unowned_archive_conflict')
        self.h.archive = []
        (self.h.root / 'library' / 'Fixture Movie (2024) {tmdb-123}').mkdir()
        self.assertEqual(self.h.execute()['reason'], 'unowned_archive_conflict')

    def test_seerr_availability_request_and_sync_blocks(self):
        self.h.meta['mediaInfo'] = {'status': 5}
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.h.meta['mediaInfo'] = {'requests': [{'id': 1}]}
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.h.meta.pop('mediaInfo'); self.h.library_sync[1]['enabled'] = True
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_queue_blocks(self):
        for item in [{'movieId': 208}, {'movieId': None}]:
            self.h.queue = [item]
            self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_library_scope_change_blocks(self):
        self.h.sections[1]['Location'][0]['path'] = '/movies'
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_authorization_and_fingerprint_required(self):
        self.assertEqual(self.h.controller.archive_movie(123, execute='true')['decision'], 'blocked')
        self.assertEqual(self.h.controller.archive_movie(123, execute=True)['decision'], 'blocked')
        p = self.h.controller.plan_archive_movie(123)
        self.h.managed[0]['qualityProfileId'] = 99
        self.assertEqual(self.h.controller.archive_movie(123, execute=True, plan_fingerprint=p['plan_fingerprint'])['decision'], 'blocked')
        self.h.config['execution_allowlist'] = []
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_eligibility_rechecked_before_publish(self):
        self.h.on_render = lambda: self.h.managed[0].update(hasFile=True)
        self.assertEqual(self.h.execute()['decision'], 'needs_review')
        self.assertEqual(list((self.h.root / 'library').iterdir()), [])
        self.assertEqual(self.h.scan_count, 0)

    def test_uncertain_scan_never_retried(self):
        self.h.fail_scan = True
        self.assertEqual(self.h.execute()['decision'], 'needs_review')
        self.h.controller = self.h.create()
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.assertEqual((self.h.render_count, self.h.scan_count), (1, 1))

    def test_indexing_timeout_no_rescan(self):
        self.h.index = False
        self.assertEqual(self.h.execute()['decision'], 'needs_review')
        self.assertEqual(self.h.execute()['decision'], 'blocked')
        self.assertEqual(self.h.scan_count, 1)

    def test_wrong_metadata_not_accepted(self):
        self.h.bad_metadata = True
        self.assertEqual(self.h.execute()['decision'], 'needs_review')

    def test_modified_placeholder_not_overwritten(self):
        r = self.h.execute(); Path(r['file']).write_bytes(b'changed')
        self.assertEqual(self.h.controller.archive_movie(123, execute=True)['decision'], 'blocked')
        self.assertEqual(self.h.render_count, 1)

    def test_concurrent_execution_one_reservation(self):
        p = self.h.controller.plan_archive_movie(123)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda c: c.archive_movie(123, execute=True, plan_fingerprint=p['plan_fingerprint']), [self.h.create(), self.h.create()]))
        self.assertEqual((self.h.render_count, self.h.scan_count), (1, 1))

    def test_symlink_folder_refused(self):
        (self.h.root / 'library' / 'Fixture Movie (2024) {tmdb-123}').symlink_to(self.h.root / 'movies')
        self.assertEqual(self.h.execute()['decision'], 'blocked')

    def test_isolation_and_permissions(self):
        c = dict(self.h.config); c['forbidden_host_roots'] = [str(self.h.root)]
        with self.assertRaises(Blocked): Archive(c, self.h, self.h, self.h, None, None)
        (self.h.root / 'state').chmod(0o755)
        with self.assertRaises(Blocked): self.h.create()

    def test_malformed_source_is_sanitized(self):
        self.h.controller.identity_reader = lambda _: (_ for _ in ()).throw(Exception('private-token-path'))
        self.assertNotIn('private-token', json.dumps(self.h.execute()))

    def test_read_api_rejects_embedded_credentials(self):
        with self.assertRaises(Blocked): ReadAPI('http://user:secret@example.invalid', 'key')

    def test_renderer_args_and_probe(self):
        probe = {'streams': [{'codec_name': 'h264', 'pix_fmt': 'yuv420p'}], 'format': {'duration': '60'}}
        replies = [subprocess.CompletedProcess([], 0, b'1234ftyp' + b'x' * 2048, b''), subprocess.CompletedProcess([], 0, json.dumps(probe).encode(), b'')]
        with patch('archive.subprocess.run', side_effect=replies) as run:
            render_synthetic({'title': "Film'; evil", 'year': 2024})
            args = run.call_args_list[0].args[0]
            self.assertIn('pipe:1', args); self.assertIn('ARCHIVE PLACEHOLDER', args[args.index('-vf') + 1])
            self.assertFalse(run.call_args_list[0].kwargs.get('shell', False))

    def test_database_reader_provenance_and_ambiguity(self):
        p = self.h.root / 'fixture.db'
        db = sqlite3.connect(p)
        db.executescript('CREATE TABLE movies(id INTEGER,title TEXT,year INTEGER); CREATE TABLE user_movies(movie_id INTEGER,watched INTEGER); CREATE TABLE external_ids(movie_id INTEGER,source TEXT,external_id TEXT);')
        db.executescript("INSERT INTO movies VALUES(1,'Fixture',2024); INSERT INTO user_movies VALUES(1,1); INSERT INTO external_ids VALUES(1,'tmdb','123'),(1,'imdb','tt1234567');")
        db.commit()
        before = p.read_bytes()
        self.assertTrue(watched_identity(p, 123)['watched']); self.assertEqual(p.read_bytes(), before)
        db.execute("INSERT INTO external_ids VALUES(2,'imdb','tt1234567')"); db.commit()
        with self.assertRaises(Blocked): watched_identity(p, 123)
        db.close()


if __name__ == '__main__':
    unittest.main()
