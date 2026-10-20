import { get_movie_status } from './get_movie_status.mjs';
import { positiveId, imdbId, normalizeTitle, normalizeQuery } from './model.mjs';

const fail = () => { throw new Error('seerr_unavailable'); };
const id = value => { const n = positiveId(value); if (n === null) fail(); return n; };
const integer = (n, min = 0) => { if (!Number.isSafeInteger(n) || n < min) fail(); return n; };
const display = value => { if (typeof value !== 'string' || !value.trim() || value.length > 500) fail(); return value.trim(); };
const releaseYear = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? Number(value.slice(0, 4)) : null;
const status = (value, max) => { if (!Number.isInteger(value) || value < 1 || value > max) fail(); return value; };

// Private client: callers receive only the preflighted controller below, not HTTP access.
class SeerrClient {
  constructor({ baseUrl, apiKey, timeoutMs = 10000, fetchImpl = fetch }) {
    try {
      this.base = new URL(baseUrl);
      if (!['http:', 'https:'].includes(this.base.protocol) || this.base.username || this.base.password || this.base.search || this.base.hash) fail();
      if (typeof apiKey !== 'string' || !apiKey.trim() || !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) fail();
      this.base.pathname = this.base.pathname.replace(/\/$/, '') + '/';
      this.key = apiKey; this.timeout = timeoutMs; this.fetch = fetchImpl;
    } catch { fail(); }
  }
  async #read(path, body) {
    try {
      const response = await this.fetch(new URL(`api/v1/${path}`, this.base), {
        method: body ? 'POST' : 'GET', redirect: 'error', signal: AbortSignal.timeout(this.timeout),
        headers: { 'X-Api-Key': this.key, Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) fail();
      let size = 0; const chunks = [];
      for await (const chunk of response.body) {
        size += chunk.length; if (size > 4 * 1024 * 1024) fail(); chunks.push(chunk);
      }
      const raw = Buffer.concat(chunks).toString('utf8');
      if (raw.includes(this.key)) fail();
      return JSON.parse(raw);
    } catch { fail(); }
  }
  async access() {
    const about = await this.#read('settings/about');
    const me = await this.#read('auth/me');
    // Full request visibility is necessary for replay safety. No auto-elevation.
    return about.version === '3.4.1' && Number.isSafeInteger(me.permissions) && (me.permissions & 2) === 2;
  }
  async search(title) {
    const rows = [];
    let pages = 1;
    for (let page = 1; page <= pages; page++) {
      const r = await this.#read(`search?query=${encodeURIComponent(title)}&page=${page}`);
      integer(r.totalPages); integer(r.totalResults);
      if (r.page !== page || r.totalPages > 10 || !Array.isArray(r.results) || r.results.length > 100) fail();
      if (page > 1 && r.totalPages !== pages) fail();
      pages = r.totalPages;
      rows.push(...r.results.map(m => ({ tmdb_id: id(m.id), type: m.mediaType,
        title: m.title ?? m.name ?? '', original_title: m.originalTitle ?? m.originalName ?? '', year: releaseYear(m.releaseDate ?? m.firstAirDate) })));
      if (page === pages || pages === 0) { if (rows.length !== r.totalResults) fail(); }
    }
    return rows;
  }
  #request(r, tmdb) {
    if (r.type !== 'movie' || typeof r.is4k !== 'boolean') fail();
    if (r.media && (r.media.mediaType !== 'movie' || id(r.media.tmdbId) !== tmdb)) fail();
    return { id: id(r.id), tmdb_id: tmdb, status: status(r.status, 5), is4k: r.is4k };
  }
  async movie(tmdb) {
    const m = await this.#read(`movie/${id(tmdb)}`);
    if (id(m.id) !== tmdb || (m.mediaType && m.mediaType !== 'movie')) fail();
    const info = m.mediaInfo;
    if (info && (info.mediaType !== 'movie' || id(info.tmdbId) !== tmdb || !Array.isArray(info.requests))) fail();
    return { tmdb_id: tmdb, title: display(m.title), original_title: display(m.originalTitle ?? m.title), year: releaseYear(m.releaseDate),
      imdb_id: imdbId(m.imdbId), status: info ? status(info.status, 7) : 1,
      status4k: info ? status(info.status4k, 7) : 1,
      requests: info ? info.requests.map(r => this.#request(r, tmdb)) : [] };
  }
  async requests() {
    const rows = []; let total;
    for (let skip = 0; skip <= 1000; skip += 100) {
      const r = await this.#read(`request?take=100&skip=${skip}&filter=all&mediaType=movie`);
      const count = integer(r.pageInfo?.results);
      if (count > 1000 || (total !== undefined && total !== count) || !Array.isArray(r.results) || r.results.length > 100) fail();
      if (!Array.isArray(r.serviceErrors?.radarr) || r.serviceErrors.radarr.length) fail();
      total = count;
      rows.push(...r.results.map(x => this.#request(x, id(x.media?.tmdbId))));
      if (rows.length >= total) { if (rows.length !== total || new Set(rows.map(x => x.id)).size !== rows.length) fail(); return rows; }
      if (r.results.length !== 100) fail();
    }
    fail();
  }
  async policy() {
    const servers = await this.#read('service/radarr');
    if (!Array.isArray(servers)) fail();
    const defaults = servers.filter(s => s.isDefault === true && s.is4k === false);
    if (defaults.length !== 1) return false;
    const s = defaults[0];
    integer(s.id);
    if (!Number.isSafeInteger(s.activeProfileId) || s.activeProfileId <= 0 || typeof s.activeDirectory !== 'string' || !s.activeDirectory) return false;
    const details = await this.#read(`service/radarr/${s.id}`);
    return details.server?.id === s.id && details.server.isDefault === true && details.server.is4k === false &&
      details.server.activeProfileId === s.activeProfileId && details.server.activeDirectory === s.activeDirectory &&
      Array.isArray(details.profiles) && details.profiles.some(p => p.id === s.activeProfileId) &&
      Array.isArray(details.rootFolders) && details.rootFolders.some(p => p.path === s.activeDirectory);
  }
  async submit(tmdb) {
    const r = await this.#read('request', { mediaType: 'movie', mediaId: id(tmdb), is4k: false });
    if (!r.media) fail();
    return this.#request(r, tmdb);
  }
  async readRequest(requestId, tmdb) {
    const r = await this.#read(`request/${id(requestId)}`);
    if (!r.media || r.id !== requestId) fail();
    return this.#request(r, tmdb);
  }
}

// Read-only reuse by the separate reacquisition boundary. No submit capability escapes.
export async function readSeerrReacquisitionSafety(config, tmdb) {
  const client = new SeerrClient(config);
  if (!await client.access()) throw new Error('seerr_unavailable');
  const movie = await client.movie(tmdb);
  const requests = await client.requests();
  return { movie, requests: requests.filter(r => r.tmdb_id === tmdb) };
}

/** Read adapters are trusted application dependencies, never user query fields. */
export function createMovieRequestController({ seerr, movieIntelligence, radarr, clock = () => new Date() }) {
  // Construct lazily so invalid configuration also produces a sanitized audit result.
  let client; let busy = false;
  const uncertain = new Set();
  const completed = new Map();
  const result = (decision, reason, movie = null, evidence = null, attempted = false, request = null, action = 'plan') => ({
    contract_version: '0.1', decision, reason,
    identity: movie ? { tmdb_id: movie.tmdb_id, title: movie.title, year: movie.year } : null,
    evidence,
    intended_action: decision === 'requestable' ? { method: 'POST', endpoint: '/api/v1/request', body: { mediaType: 'movie', mediaId: movie.tmdb_id, is4k: false } } : null,
    request,
    audit: { contract_version: '0.1', timestamp: clock().toISOString(), action,
      tmdb_id: movie?.tmdb_id ?? null, decision, reason, mutation_attempted: attempted,
      preflight_decision: attempted ? 'requestable' : decision,
      request_id: request?.id ?? null, request_status: request?.status ?? null },
  });
  async function plan(input) {
    let movie;
    try {
      if (!input || Object.keys(input).some(k => !['tmdb_id', 'title', 'year'].includes(k))) return result('blocked', 'invalid_input', null, null, false, null, 'blocked');
      const q = normalizeQuery(input);
      if (!q.external_ids.tmdb && (!q.title || !q.year)) return result('blocked', 'title_requires_year', null, null, false, null, 'blocked');
      client ??= new SeerrClient(seerr);
      if (!await client.access()) return result('blocked', 'unsupported_version_or_visibility', null, null, false, null, 'blocked');
      let tmdb = q.external_ids.tmdb;
      if (!tmdb) {
        const hits = (await client.search(q.title)).filter(r => r.year === q.year && [r.title, r.original_title].some(t => normalizeTitle(t) === normalizeTitle(q.title)));
        if (hits.length > 1) return result('ambiguous', 'multiple_search_candidates', null, null, false, null, 'blocked');
        if (hits.length !== 1 || hits[0].type !== 'movie') return result('blocked', 'no_unique_movie', null, null, false, null, 'blocked');
        tmdb = hits[0].tmdb_id;
      }
      movie = await client.movie(tmdb);
      if ((q.year && q.year !== movie.year) || (q.title && ![movie.title, movie.original_title].some(t => normalizeTitle(t) === normalizeTitle(q.title))))
        return result('blocked', 'resolved_identity_conflict', movie, null, false, null, 'blocked');
      const source = await get_movie_status({ tmdb_id: tmdb, ...(movie.imdb_id ? { imdb_id: movie.imdb_id } : {}), ...(movie.year ? { year: movie.year } : {}) }, { movieIntelligence, radarr });
      const evidence = { read_state: source.summary.state,
        movie_intelligence: { status: source.sources.movie_intelligence.status, count: source.sources.movie_intelligence.candidates.length },
        radarr: { status: source.sources.radarr.status, count: source.sources.radarr.candidates.length }, seerr_status: movie.status };
      if (['ambiguous', 'unresolved'].includes(source.summary.state)) return result(source.summary.state === 'ambiguous' ? 'ambiguous' : 'unresolved', 'read_identity_or_source_unresolved', movie, evidence, false, null, 'blocked');
      // Independently cross-check Seerr against each source, not only against each other.
      for (const s of Object.values(source.sources)) for (const row of s.candidates) {
        if ((row.external_ids.tmdb && row.external_ids.tmdb !== tmdb) || (row.external_ids.imdb && movie.imdb_id && row.external_ids.imdb !== movie.imdb_id))
          return result('blocked', 'source_identity_conflict', movie, evidence, false, null, 'blocked');
      }
      const requests = (await client.requests()).filter(r => r.tmdb_id === tmdb);
      requests.push(...movie.requests);
      if ([movie.status, movie.status4k].includes(6)) return result('blocked', 'seerr_blocklisted', movie, evidence, false, null, 'blocked');
      if (source.sources.radarr.candidates.some(r => r.has_file === true) || [movie.status, movie.status4k].includes(5)) return result('already_available', 'file_or_seerr_available', movie, evidence, false, null, 'noop');
      // Conservative across variants: do not create another copy or revive failed/declined requests.
      if (requests.some(r => [1, 2, 5].includes(r.status)) || [movie.status, movie.status4k].some(s => [2, 3, 4].includes(s)))
        return result('already_requested', 'existing_request_or_processing', movie, evidence, false, requests[0] ?? null, 'noop');
      if (requests.length) return result('blocked', 'failed_or_declined_request_requires_review', movie, evidence, false, requests[0], 'blocked');
      if (source.sources.radarr.candidates.length) return result('already_managed', 'radarr_manages_movie', movie, evidence, false, null, 'noop');
      if (!await client.policy()) return result('blocked', 'media_policy_dependency', movie, evidence, false, null, 'blocked');
      return result('requestable', 'stable_identity_and_clear_preflight', movie, evidence);
    } catch { return result('unresolved', 'source_or_configuration_unavailable', movie, null, false, null, 'blocked'); }
  }
  async function request_movie(input, options = {}) {
    if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).some(k => !['execute', 'previous_unknown'].includes(k)) ||
        (options.execute !== undefined && typeof options.execute !== 'boolean') ||
        (options.previous_unknown !== undefined && typeof options.previous_unknown !== 'boolean')) return result('blocked', 'invalid_options', null, null, false, null, 'blocked');
    if (busy) return result('blocked', 'invocation_in_progress', null, null, false, null, 'blocked');
    busy = true;
    try {
      const p = await plan(input);
      if (p.decision !== 'requestable') return p;
      const movie = p.identity;
      if (completed.has(movie.tmdb_id)) return result('already_requested', 'previous_request_confirmed', movie, p.evidence, false, completed.get(movie.tmdb_id), 'noop');
      if (uncertain.has(movie.tmdb_id) || options.previous_unknown === true)
        return result('unknown_after_submit', 'previous_submission_requires_reconciliation', movie, p.evidence, false, null, 'blocked');
      if (options.execute !== true) return p;
      // Refresh all preflight evidence immediately before submitting, never accept a supplied plan.
      const fresh = await plan({ tmdb_id: movie.tmdb_id });
      if (fresh.decision !== 'requestable') return fresh;
      uncertain.add(movie.tmdb_id);
      try {
        const submitted = await client.submit(movie.tmdb_id);
        if (submitted.is4k || ![1, 2, 5].includes(submitted.status)) fail();
        const readback = await client.readRequest(submitted.id, movie.tmdb_id);
        if (readback.is4k || ![1, 2, 5].includes(readback.status)) fail();
        uncertain.delete(movie.tmdb_id);
        completed.set(movie.tmdb_id, readback);
        return result('requested', 'request_identity_readback_confirmed', movie, fresh.evidence, true, readback, 'request');
      } catch { return result('unknown_after_submit', 'submission_or_readback_uncertain', movie, fresh.evidence, true, null, 'request'); }
    } finally { busy = false; }
  }
  return Object.freeze({ plan_movie_request: plan, request_movie });
}
