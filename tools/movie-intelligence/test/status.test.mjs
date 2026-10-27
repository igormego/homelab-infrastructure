import test from 'node:test';
import assert from 'node:assert/strict';
import { get_movie_status } from '../get_movie_status.mjs';
import { fixtureAdapters } from '../fixtures.mjs';
import { intelligenceRecord as mi, radarrRecord } from '../model.mjs';

const rad = row => radarrRecord({ id: 1, title: 'Movie', ...row }, new Map());
const adapters = (a = [], b = []) => ({ movieIntelligence: () => a, radarr: () => b });
const check = (q, a = fixtureAdapters()) => get_movie_status(q, a);

test('Heretik: personal state preserved, absent Radarr is not unwatched', async () => {
  const r = await check({ title: 'Heretik', year: 2024 });
  assert.equal(r.summary.state, 'single_source_only');
  const [m] = r.sources.movie_intelligence.candidates;
  assert.equal(m.watched, true); assert.equal(m.watchlist, false);
  assert.equal(m.personal_rating, 4); assert.equal(m.rated_at, '2025-01-02');
  assert.equal(m.external_ids.csfd, 900001);
  assert.deepEqual(r.sources.radarr.candidates, []);
});

test('Scarface stable TMDb joins translated title and returns library profile', async () => {
  const r = await check({ tmdb_id: 111 });
  assert.equal(r.summary.state, 'confirmed_match');
  assert.equal(r.summary.matched_by, 'tmdb');
  assert.equal(r.identity.external_ids.imdb, 'tt0086250');
  const [m] = r.sources.radarr.candidates;
  assert.equal(m.monitored, true); assert.equal(m.has_file, true);
  assert.deepEqual(m.quality_profile, { id: 9, name: 'UHD Bluray + WEB' });
});

test('title/year fallback discovers stable match across translated titles', async () => {
  const r = await check({ title: '  ZJIZVENÁ   TVÁŘ ', year: 1983 });
  assert.equal(r.summary.state, 'confirmed_match');
});

test('Radarr-only and neither-source results', async () => {
  assert.equal((await check({ tmdb_id: 900002 })).summary.state, 'single_source_only');
  assert.equal((await check({ title: 'Absent Fixture' })).summary.state, 'not_found');
});

test('remakes require disambiguation; year narrows candidates', async () => {
  const r = await check({ title: 'Scarface' });
  assert.equal(r.summary.state, 'ambiguous');
  assert.equal(r.sources.movie_intelligence.candidates.length, 2);
  assert.equal((await check({ title: 'Scarface', year: 1983 })).summary.state, 'confirmed_match');
});

test('same title/year without shared IDs never confirms a match', async () => {
  const r = await check({ title: 'Movie', year: 2000 }, adapters([mi({ id: 1, title: 'Movie', year: 2000 })], [rad({ year: 2000 })]));
  assert.equal(r.summary.state, 'unresolved'); assert.equal(r.identity.external_ids, null);
});

test('same TMDb but conflicting IMDb fails closed', async () => {
  const r = await check({ tmdb_id: 111 }, adapters([mi({ id: 1, title: 'A', tmdb_id: 111, imdb_id: 'tt0086250' })], [rad({ tmdbId: 111, imdbId: 'tt0023427' })]));
  assert.equal(r.summary.reason, 'source_identity_conflict');
});

test('same IMDb never overrides conflicting TMDb IDs', async () => {
  const r = await check({ title: 'Movie' }, adapters([mi({ id: 1, title: 'Movie', tmdb_id: 111, imdb_id: 'tt0086250' })], [rad({ tmdbId: 877, imdbId: 'tt0086250' })]));
  assert.equal(r.summary.state, 'unresolved');
});

test('conflicting query IDs and conflicting year cannot be silently ignored', async () => {
  assert.equal((await check({ tmdb_id: 111, imdb_id: 'tt0023427' })).summary.reason, 'query_identity_conflict');
  assert.equal((await check({ tmdb_id: 111, year: 1932 })).summary.reason, 'query_identity_conflict');
});

test('IMDb is usable when TMDb is missing; year disagreement stays unresolved', async () => {
  const a = mi({ id: 1, title: 'Movie', imdb_id: 'tt0086250', year: 1983 });
  assert.equal((await check({ imdb_id: 'tt0086250' }, adapters([a], [rad({ imdbId: 'tt0086250', year: 1983 })]))).summary.matched_by, 'imdb');
  assert.equal((await check({ title: 'Movie' }, adapters([a], [rad({ imdbId: 'tt0086250', year: 1932 })]))).summary.state, 'unresolved');
});

test('duplicate stable IDs and duplicate one-source titles remain ambiguous', async () => {
  const a = mi({ id: 1, title: 'Movie', tmdb_id: 111 });
  assert.equal((await check({ tmdb_id: 111 }, adapters([a, { ...a, id: 2 }]))).summary.state, 'ambiguous');
});

test('duplicate requested TMDb IDs in either source stay ambiguous despite a unique peer', async () => {
  const personal = id => mi({ id, title: 'Personal title', tmdb_id: 111, watched: true });
  const library = id => rad({ id, title: 'Library title', tmdbId: 111, hasFile: true });
  for (const [a, b] of [
    [[personal(2), personal(1)], [library(3)]],
    [[personal(3)], [library(2), library(1)]],
  ]) {
    const r = await check({ tmdb_id: 111 }, adapters(a, b));
    assert.deepEqual(r.summary, { state: 'ambiguous', reason: 'multiple_candidates', matched_by: null });
    assert.deepEqual(r.identity, { external_ids: null, enrichment: { status: 'not_requested' } });
    assert.deepEqual(r.sources.movie_intelligence.candidates, [...a].sort((x, y) => x.id - y.id));
    assert.deepEqual(r.sources.radarr.candidates, [...b].sort((x, y) => x.id - y.id));
  }
});

test('a shared stable ID cannot enrich identities with a conflicting non-null stable ID', async () => {
  for (const [personalIds, libraryIds] of [
    [{ tmdb_id: 111, imdb_id: 'tt0086250' }, { tmdbId: 111, imdbId: 'tt0023427' }],
    [{ imdb_id: 'tt0086250', tmdb_id: 111 }, { imdbId: 'tt0086250', tmdbId: 877 }],
    [{ tmdb_id: 111, csfd_id: 900001 }, { tmdbId: 111, csfd_id: 900002 }],
  ]) {
    const a = mi({ id: 1, title: 'Movie', ...personalIds });
    const b = rad(libraryIds);
    // Title discovery exercises cross-source reconciliation without a query-ID conflict.
    const r = await check({ title: 'Movie' }, adapters([a], [b]));
    assert.deepEqual(r.summary, { state: 'unresolved', reason: 'source_identity_conflict', matched_by: null });
    assert.deepEqual(r.identity, { external_ids: null, enrichment: { status: 'not_requested' } });
    assert.deepEqual(r.sources.movie_intelligence.candidates, [a]);
    assert.deepEqual(r.sources.radarr.candidates, [b]);
  }
});

test('rejected adapter Promises preserve peer evidence and sanitize source errors', async () => {
  const rawError = 'private-adapter-error-sentinel';
  const a = mi({ id: 1, title: 'Movie', tmdb_id: 111, watched: true });
  const b = rad({ tmdbId: 111, hasFile: true });
  for (const failed of ['movie_intelligence', 'radarr']) {
    const injected = adapters([a], [b]);
    injected[failed === 'radarr' ? 'radarr' : 'movieIntelligence'] = () => Promise.reject(new Error(rawError));
    const r = await check({ tmdb_id: 111 }, injected);
    assert.deepEqual(r.summary, { state: 'unresolved', reason: 'source_unavailable', matched_by: null });
    assert.deepEqual(r.sources[failed], { status: 'error', error: `${failed}_unavailable`, candidates: [] });
    const peer = failed === 'radarr' ? 'movie_intelligence' : 'radarr';
    assert.deepEqual(r.sources[peer], { status: 'ok', error: null, candidates: [failed === 'radarr' ? a : b] });
    assert.deepEqual(r.identity, { external_ids: null, enrichment: { status: 'not_requested' } });
    assert.ok(!JSON.stringify(r).includes(rawError));
  }
});

test('non-array adapter results are unavailable rather than empty or confirmed', async () => {
  const rawData = 'private-malformed-data-sentinel';
  const a = mi({ id: 1, title: 'Movie', tmdb_id: 111, watchlist: true });
  const b = rad({ tmdbId: 111, monitored: true });
  for (const malformed of [null, undefined, rawData, { error: rawData, records: [a] }]) {
    for (const failed of ['movie_intelligence', 'radarr']) {
      const injected = adapters([a], [b]);
      injected[failed === 'radarr' ? 'radarr' : 'movieIntelligence'] = async () => malformed;
      const r = await check({ tmdb_id: 111 }, injected);
      assert.deepEqual(r.summary, { state: 'unresolved', reason: 'source_unavailable', matched_by: null });
      assert.deepEqual(r.sources[failed], { status: 'error', error: `${failed}_unavailable`, candidates: [] });
      const peer = failed === 'radarr' ? 'movie_intelligence' : 'radarr';
      assert.deepEqual(r.sources[peer], { status: 'ok', error: null, candidates: [failed === 'radarr' ? a : b] });
      assert.deepEqual(r.identity, { external_ids: null, enrichment: { status: 'not_requested' } });
      assert.ok(!JSON.stringify(r).includes(rawData));
    }
  }
});

test('ID query never substitutes a different title match', async () => {
  assert.equal((await check({ tmdb_id: 999999, title: 'Scarface' })).summary.state, 'not_found');
});

test('ČSFD can confirm shared identity without claiming TMDb enrichment', async () => {
  const row = mi({ id: 1, title: 'Movie', csfd_id: 900001 });
  const r = await check({ csfd_id: 900001 }, adapters([row], [{ ...rad({}), external_ids: { tmdb: null, imdb: null, csfd: 900001 } }]));
  assert.equal(r.summary.matched_by, 'csfd');
  assert.equal(r.identity.external_ids.tmdb, null);
  assert.equal(r.identity.enrichment.status, 'not_requested');
});

test('source failure is unresolved with partial evidence and no raw error', async () => {
  const secret = 'fixture-secret-not-a-real-key';
  const r = await check({ tmdb_id: 111 }, { ...fixtureAdapters(), radarr: () => { throw new Error(secret); } });
  assert.equal(r.summary.state, 'unresolved');
  assert.equal(r.sources.movie_intelligence.candidates.length, 1);
  assert.equal(r.sources.radarr.status, 'error');
  assert.ok(!JSON.stringify(r).includes(secret));
});

test('invalid queries fail before invoking adapters', async () => {
  for (const q of [{}, { year: 2024 }, { tmdb_id: 0 }, { imdb_id: 'bad' }, { title: 'Movie', sql: 'DELETE' }]) {
    await assert.rejects(check(q, { movieIntelligence: () => assert.fail(), radarr: () => assert.fail() }), /invalid_query/);
  }
});
