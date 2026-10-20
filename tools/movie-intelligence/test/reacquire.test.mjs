import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createMovieReacquisitionController } from '../reacquire.mjs';
import { reacquireJournal } from '../reacquire-journal.mjs';
import { spawn } from 'node:child_process';

const query = { tmdb_id: 123, imdb_id: 'tt1234567' };
const response = data => new Response(JSON.stringify(data));
function harness(t) {
  const directory = mkdtempSync(join(tmpdir(), 'reacquire-test-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const h = { calls: [], override: null, mi: [], plexRows: [], parts: [{ exists: false }],
    movies: [{ id: 9, tmdbId: 123, imdbId: 'tt1234567', title: 'Synthetic Film', year: 2020,
      monitored: false, hasFile: false, movieFileId: 0, sizeOnDisk: 0, qualityProfileId: 42,
      rootFolderPath: '/synthetic', path: '/synthetic/film', minimumAvailability: 'released', tags: [5], customField: { preserve: true } }],
    profiles: [{ id: 42, name: 'Synthetic UHD', cutoff: 7, upgradeAllowed: true }],
    roots: [{ path: '/synthetic', accessible: true }], requests: [], queue: [], commands: [], mediaInfo: null, directory,
  };
  h.fetch = async (url, options) => {
    const u = new URL(url); const p = u.pathname + u.search;
    h.calls.push({ host: u.hostname, path: p, method: options.method, body: options.body && JSON.parse(options.body) });
    if (h.override) { const r = await h.override(p, options, u); if (r !== undefined) return r; }
    if (u.hostname === 'radarr.invalid') {
      if (p === '/api/v3/system/status') return response({ version: '6.1.1.10360' });
      if (p === '/api/v3/movie') return response(h.movies);
      if (p === '/api/v3/qualityprofile') return response(h.profiles);
      if (p === '/api/v3/rootfolder') return response(h.roots);
      if (p.startsWith('/api/v3/queue?')) return response({ page: 1, pageSize: 100, totalRecords: h.queue.length, records: h.queue });
      if (p === '/api/v3/command' && options.method === 'GET') return response(h.commands);
      if (p === '/api/v3/movie/editor' && options.method === 'PUT') {
        assert.deepEqual(JSON.parse(options.body), { movieIds: [9], monitored: true });
        h.movies[0].monitored = true; return response([h.movies[0]]);
      }
      if (p === '/api/v3/command' && options.method === 'POST') {
        assert.deepEqual(JSON.parse(options.body), { name: 'MoviesSearch', movieIds: [9] });
        h.command = { id: 70, name: 'MoviesSearch', body: { movieIds: [9] }, status: 'queued' }; return response(h.command);
      }
      if (p === '/api/v3/command/70') return response(h.command);
    }
    if (u.hostname === 'seerr.invalid') {
      assert.equal(options.method, 'GET');
      if (p === '/api/v1/settings/about') return response({ version: '3.4.1' });
      if (p === '/api/v1/auth/me') return response({ permissions: 2 });
      if (p === '/api/v1/movie/123') return response({ id: 123, title: 'Synthetic Film', imdbId: 'tt1234567', releaseDate: '2020-01-01', mediaInfo: h.mediaInfo });
      if (p.startsWith('/api/v1/request?')) return response({ pageInfo: { results: h.requests.length }, results: h.requests, serviceErrors: { radarr: [] } });
    }
    if (u.hostname === 'plex.invalid') {
      assert.equal(options.method, 'GET');
      if (p === '/library/sections') return response({ MediaContainer: { Directory: [{ key: '1', type: 'movie', title: 'Movies' }, { key: '4', type: 'movie', title: 'Watched — Test' }] } });
      if (p.startsWith('/library/sections/1/all?')) return response({ MediaContainer: { totalSize: h.plexRows.length, size: h.plexRows.length, Metadata: h.plexRows } });
      if (p === '/library/metadata/30?checkFiles=1&includeGuids=1') return response({ MediaContainer: { Metadata: [{ ratingKey: '30', librarySectionID: '1', Guid: h.plexRows[0]?.Guid, Media: [{ Part: h.parts }] }] } });
    }
    throw new Error('unexpected fixture endpoint');
  };
  const config = host => ({ baseUrl: `http://${host}.invalid`, apiKey: 'synthetic-secret-credential', fetchImpl: h.fetch });
  h.config = { radarr: config('radarr'), seerr: config('seerr'), plex: { ...config('plex'), moviesLibraryId: 1 }, movieIntelligence: () => h.mi, journalDirectory: directory };
  h.create = () => createMovieReacquisitionController(h.config);
  h.controller = h.create();
  h.writes = () => h.calls.filter(c => c.method !== 'GET');
  h.execute = async (input = query) => {
    const plan = await h.controller.plan_reacquire_movie(input);
    return h.controller.reacquire_movie(input, { execute: true, plan_fingerprint: plan.plan_fingerprint });
  };
  return h;
}

test('default dry-run gives exact bounded plan; original fields and profile remain unchanged on execution', async t => {
  const h = harness(t); const original = structuredClone(h.movies[0]); const profiles = structuredClone(h.profiles);
  const p = await h.controller.reacquire_movie(query);
  assert.equal(p.decision, 'reacquirable'); assert.equal(p.operations.length, 2);
  assert.deepEqual(p.preserve.tags, [5]); assert.equal(h.writes().length, 0);
  assert.equal(readdirSync(h.directory).length, 0);
  const r = await h.execute(); assert.equal(r.decision, 'submitted');
  assert.deepEqual(h.movies[0], { ...original, monitored: true }); assert.deepEqual(h.profiles, profiles);
  assert.deepEqual(h.writes().map(x => [x.method, x.path]), [['PUT', '/api/v3/movie/editor'], ['POST', '/api/v3/command']]);
  assert.ok(!JSON.stringify(r).includes('synthetic-secret-credential'));
  assert.ok(!h.calls.some(c => c.path.includes('/sections/4')));
});

for (const [name, change] of [
  ['downloaded', h => { h.movies[0].hasFile = true; }],
  ['inconsistent file id', h => { h.movies[0].movieFileId = 4; }],
  ['already monitored', h => { h.movies[0].monitored = true; }],
  ['unknown file state', h => { delete h.movies[0].hasFile; }],
  ['active queue', h => { h.queue = [{ id: 1, movieId: 9 }]; }],
  ['string queue identity', h => { h.queue = [{ id: 1, movieId: '9' }]; }],
  ['unknown queue item', h => { h.queue = [{ id: 1 }]; }],
  ['active command', h => { h.commands = [{ name: 'MissingMoviesSearch', status: 'started' }]; }],
  ['duplicate TMDb', h => { h.movies.push({ ...h.movies[0], id: 10 }); }],
  ['absent Radarr', h => { h.movies = []; }],
  ['IMDb conflict', h => { h.movies[0].imdbId = 'tt7654321'; }],
  ['profile unavailable', h => { h.profiles = []; }],
  ['root unavailable', h => { h.roots[0].accessible = false; }],
  ['canonical conflict', h => { h.mi = [{ id: 1, external_ids: { tmdb: 123, imdb: 'tt7654321', csfd: null }, year: 2020 }]; }],
  ['canonical ambiguity', h => { h.mi = [1, 2].map(id => ({ id, external_ids: { tmdb: 123, imdb: 'tt1234567', csfd: null }, year: 2020 })); }],
]) test(`preflight blocks ${name} without writes`, async t => {
  const h = harness(t); change(h); assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

for (const status of [1, 2, 3, 4, 5]) test(`every existing Seerr request status ${status} blocks`, async t => {
  const h = harness(t); h.requests = [{ id: 7, type: 'movie', is4k: false, status, media: { tmdbId: 123, mediaType: 'movie' } }];
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});
for (const status of [2, 3, 4, 5, 6, 7]) test(`cached Seerr state ${status} is conservative conflict`, async t => {
  const h = harness(t); h.mediaInfo = { mediaType: 'movie', tmdbId: 123, status, status4k: 1, requests: [] };
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

test('reviewed profile/root conflicts and stale plan prevent execution', async t => {
  const h = harness(t);
  for (const extra of [{ expected_quality_profile_id: 99 }, { expected_root_folder: '/wrong' }]) {
    assert.equal((await h.execute({ ...query, ...extra })).decision, 'blocked');
  }
  const p = await h.controller.plan_reacquire_movie(query); h.movies[0].tags.push(6);
  assert.equal((await h.controller.reacquire_movie(query, { execute: true, plan_fingerprint: p.plan_fingerprint })).reason, 'reviewed_plan_required_or_changed');
  assert.equal(h.writes().length, 0);
});

for (const exists of [true, 1, '1', undefined]) test(`Plex existing or unknown part (${exists}) blocks`, async t => {
  const h = harness(t); h.plexRows = [{ ratingKey: '30', Guid: [{ id: 'tmdb://123' }] }]; h.parts = [{ exists }];
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});
test('explicitly missing main-library part is eligible, archive stays excluded', async t => {
  const h = harness(t); h.plexRows = [{ ratingKey: '30', Guid: [{ id: 'tmdb://123' }, { id: 'imdb://tt1234567' }] }];
  assert.equal((await h.execute()).decision, 'submitted');
});
test('wrong Plex section and unknown identities fail closed', async t => {
  const h = harness(t); h.config.plex.moviesLibraryId = 4; h.controller = h.create();
  assert.equal((await h.execute()).decision, 'blocked');
  h.config.plex.moviesLibraryId = 1; h.controller = h.create(); h.plexRows = [{ ratingKey: '30', Guid: [] }];
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

test('Plex metadata identity drift during file-existence check blocks', async t => {
  const h = harness(t); h.plexRows = [{ ratingKey: '30', Guid: [{ id: 'tmdb://123' }] }];
  h.override = p => p.startsWith('/library/metadata/30?') ? response({ MediaContainer: { Metadata: [{
    ratingKey: '30', librarySectionID: '1', Guid: [{ id: 'tmdb://456' }], Media: [{ Part: [{ exists: false }] }],
  }] } }) : undefined;
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

test('explicit true, approved fingerprint and persistent directory are all required', async t => {
  const h = harness(t);
  for (const options of [{ execute: 'true' }, { execute: 1 }, { execute: true }, { execute: true, force: true }]) {
    assert.equal((await h.controller.reacquire_movie(query, options)).decision, 'blocked');
  }
  h.config.journalDirectory = undefined; h.controller = h.create(); assert.equal((await h.execute()).decision, 'blocked');
  assert.equal(h.writes().length, 0);
});

for (const path of ['/api/v3/movie', '/api/v3/qualityprofile', '/api/v3/rootfolder', '/api/v3/command', '/api/v1/settings/about', '/library/sections']) {
  test(`GET failure ${path} blocks without secrets`, async t => {
    const h = harness(t); h.override = p => { if (p === path) throw new Error('synthetic-secret-credential'); };
    const r = await h.execute(); assert.equal(r.decision, 'blocked'); assert.equal(h.writes().length, 0);
    assert.ok(!JSON.stringify(r).includes('synthetic-secret-credential'));
  });
}
for (const stage of ['monitor', 'search', 'readback']) test(`uncertain ${stage} retained across controller restart, never retried`, async t => {
  const h = harness(t);
  h.override = (p, o) => {
    if ((stage === 'monitor' && o.method === 'PUT') || (stage === 'search' && o.method === 'POST') || (stage === 'readback' && p === '/api/v3/command/70'))
      throw new Error('response lost after possible write');
  };
  const r = await h.execute(); assert.equal(r.decision, 'uncertain_requires_review');
  const count = h.writes().length; h.controller = h.create(); h.movies[0].monitored = false; h.override = null;
  assert.equal((await h.controller.reacquire_movie(query, { execute: true })).decision, 'uncertain_requires_review');
  assert.equal(h.writes().length, count);
});

test('successful repeated invocation never produces a second search even after external unmonitor', async t => {
  const h = harness(t); assert.equal((await h.execute()).decision, 'submitted'); h.movies[0].monitored = false; h.controller = h.create();
  assert.equal((await h.execute()).decision, 'already_executed'); assert.equal(h.writes().length, 2);
});

test('concurrent controllers share exclusive reservation', async t => {
  const h = harness(t); const p = await h.controller.plan_reacquire_movie(query); const other = h.create();
  const results = await Promise.all([h.controller, other].map(c => c.reacquire_movie(query, { execute: true, plan_fingerprint: p.plan_fingerprint })));
  assert.equal(results.filter(r => r.decision === 'submitted').length, 1);
  assert.equal(h.writes().filter(x => x.method === 'POST').length, 1);
});

test('exclusive durable reservation also holds across OS processes', async t => {
  const h = harness(t); const module = new URL('../reacquire-journal.mjs', import.meta.url).href;
  const code = `import {reacquireJournal} from ${JSON.stringify(module)}; try { reacquireJournal(process.argv[1], 'a'.repeat(64), 123).reserve({}); process.stdout.write('won'); } catch { process.stdout.write('blocked'); }`;
  const run = () => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', code, h.directory]); let output = '';
    child.stdout.on('data', x => { output += x; }); child.on('error', reject); child.on('close', c => c === 0 ? resolve(output) : reject(new Error('child failure')));
  });
  assert.deepEqual((await Promise.all([run(), run()])).sort(), ['blocked', 'won']);
});

for (const field of ['qualityProfileId', 'rootFolderPath', 'minimumAvailability', 'tags', 'customField']) {
  test(`post-monitor drift in ${field} stops search and stays uncertain`, async t => {
    const h = harness(t); h.override = (p, o) => {
      if (o.method === 'PUT') {
        h.movies[0].monitored = true;
        h.movies[0][field] = field === 'qualityProfileId' ? 99 : field === 'tags' ? [77] : 'changed';
        return response([h.movies[0]]);
      }
    };
    assert.equal((await h.execute()).decision, 'uncertain_requires_review');
    assert.equal(h.writes().filter(x => x.method === 'POST').length, 0);
  });
}

test('truncated queue and Plex pagination never establish absence', async t => {
  const h = harness(t);
  h.override = p => p.startsWith('/api/v3/queue?') ? response({ page: 1, pageSize: 100, totalRecords: 101, records: [] }) : undefined;
  assert.equal((await h.execute()).decision, 'blocked');
  h.override = p => p.startsWith('/library/sections/1/all?') ? response({ MediaContainer: { totalSize: 101, size: 0, Metadata: [] } }) : undefined;
  assert.equal((await h.execute()).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

test('corrupt durable receipt cannot become permission to replay', t => {
  const h = harness(t); const j = reacquireJournal(h.directory, 'a'.repeat(64), 123); j.reserve({});
  writeFileSync(join(h.directory, readdirSync(h.directory)[0]), '{'); assert.throws(() => j.read());
});

test('Seerr IMDb must agree with canonical identity even when Radarr omits IMDb', async t => {
  const h = harness(t); delete h.movies[0].imdbId;
  h.mi = [{ id: 1, external_ids: { tmdb: 123, imdb: 'tt7654321', csfd: null }, year: 2020 }];
  assert.equal((await h.execute({ tmdb_id: 123 })).decision, 'blocked'); assert.equal(h.writes().length, 0);
});

for (const conflict of ['queue', 'request', 'profile']) test(`${conflict} appearing after monitor prevents search`, async t => {
  const h = harness(t); h.override = (p, o) => {
    if (o.method === 'PUT') {
      h.movies[0].monitored = true;
      if (conflict === 'queue') h.queue = [{ id: 1, movieId: 9 }];
      if (conflict === 'request') h.requests = [{ id: 7, type: 'movie', is4k: false, status: 1, media: { tmdbId: 123, mediaType: 'movie' } }];
      if (conflict === 'profile') h.profiles[0].cutoff = 99;
      return response([h.movies[0]]);
    }
  };
  assert.equal((await h.execute()).decision, 'uncertain_requires_review');
  assert.equal(h.writes().filter(x => x.method === 'POST').length, 0);
});

test('last preflight drift after reservation prevents all writes and requires review', async t => {
  const h = harness(t); const p = await h.controller.plan_reacquire_movie(query); let reads = 0;
  h.override = path => { if (path === '/api/v3/movie' && ++reads === 2) h.movies[0].tags.push(77); };
  const r = await h.controller.reacquire_movie(query, { execute: true, plan_fingerprint: p.plan_fingerprint });
  assert.equal(r.reason, 'reserved_preflight_changed_requires_review'); assert.equal(h.writes().length, 0);
  assert.equal((await h.controller.reacquire_movie(query, { execute: true })).decision, 'uncertain_requires_review');
});

test('same-controller concurrency guard is immediate', async t => {
  const h = harness(t); const p = await h.controller.plan_reacquire_movie(query);
  const first = h.controller.reacquire_movie(query, { execute: true, plan_fingerprint: p.plan_fingerprint });
  assert.equal((await h.controller.reacquire_movie(query, { execute: true, plan_fingerprint: p.plan_fingerprint })).reason, 'execution_in_progress');
  assert.equal((await first).decision, 'submitted'); assert.equal(h.writes().length, 2);
});

test('failed or wrong-target command readback is uncertain and not resubmitted', async t => {
  for (const command of [
    { id: 70, name: 'MoviesSearch', body: { movieIds: [9] }, status: 'failed' },
    { id: 70, name: 'MoviesSearch', body: { movieIds: [10] }, status: 'queued' },
  ]) {
    const h = harness(t); h.override = p => p === '/api/v3/command/70' ? response(command) : undefined;
    assert.equal((await h.execute()).decision, 'uncertain_requires_review');
    assert.equal((await h.controller.reacquire_movie(query, { execute: true })).decision, 'uncertain_requires_review');
    assert.equal(h.writes().filter(x => x.method === 'POST').length, 1);
  }
});

test('HTTP failure status, malformed JSON, credential echo and redirect are never retried', async t => {
  for (const reply of [() => new Response('', { status: 500 }), () => new Response('{'),
    () => response({ token: 'synthetic-secret-credential' }), () => new Response('', { status: 302, headers: { Location: 'http://elsewhere.invalid' } })]) {
    const h = harness(t); h.override = (p, o) => {
      assert.equal(o.redirect, 'error');
      if (o.method === 'PUT') return reply();
    };
    assert.equal((await h.execute()).decision, 'uncertain_requires_review'); assert.equal(h.writes().length, 1);
  }
});
