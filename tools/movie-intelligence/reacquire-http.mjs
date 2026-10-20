import { createHash } from 'node:crypto';
import { positiveId, imdbId } from './model.mjs';

const fail = () => { throw new Error('source_unavailable'); };
export const requiredId = x => { const n = positiveId(x); if (!n) fail(); return n; };
export const array = x => { if (!Array.isArray(x) || x.length > 100000) fail(); return x; };

// No retries, redirects, raw errors, credentials in URLs, or unbounded bodies.
export function jsonClient({ baseUrl, apiKey, timeoutMs = 10000, fetchImpl = fetch }, prefix, header = 'X-Api-Key') {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash ||
      typeof apiKey !== 'string' || !apiKey.trim() || !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) fail();
  base.pathname = base.pathname.replace(/\/$/, '') + '/';
  return {
    scope: createHash('sha256').update(base.href).digest('hex'),
    async call(path, method = 'GET', body) {
      try {
        const response = await fetchImpl(new URL(prefix + path, base), {
          method, redirect: 'error', signal: AbortSignal.timeout(timeoutMs),
          headers: { [header]: apiKey, Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        if (!response.ok) fail();
        let size = 0; const chunks = [];
        for await (const chunk of response.body) { size += chunk.length; if (size > 32 * 1024 * 1024) fail(); chunks.push(chunk); }
        const raw = Buffer.concat(chunks).toString('utf8');
        if (raw.includes(apiKey)) fail();
        return JSON.parse(raw);
      } catch { fail(); }
    },
  };
}

export async function radarrQueue(client) {
  const rows = []; let total;
  for (let page = 1; page <= 1000; page++) {
    const r = await client.call(`queue?page=${page}&pageSize=100&includeUnknownMovieItems=true`);
    if (r.page !== page || r.pageSize !== 100 || !Number.isSafeInteger(r.totalRecords) || r.totalRecords < 0 ||
        r.totalRecords > 100000 || (total !== undefined && total !== r.totalRecords)) fail();
    total = r.totalRecords; const batch = array(r.records);
    if (batch.length > 100) fail(); rows.push(...batch);
    if (rows.length === total) {
      if (new Set(rows.map(x => requiredId(x.id))).size !== total) fail();
      return rows;
    }
    if (rows.length > total || batch.length !== 100) fail();
  }
  fail();
}

// Only the real Movies section is consulted. Archive libraries are never scanned.
export async function plexConflict(config, tmdb, imdb) {
  const client = jsonClient(config, '', 'X-Plex-Token');
  const library = String(requiredId(config.moviesLibraryId));
  const sections = array((await client.call('library/sections')).MediaContainer?.Directory);
  const selected = sections.filter(s => String(s.key) === library);
  if (selected.length !== 1 || selected[0].type !== 'movie' || selected[0].title !== 'Movies') fail();
  const rows = []; let total;
  for (let start = 0; start < 100000; start += 100) {
    const c = (await client.call(`library/sections/${library}/all?type=1&includeGuids=1&X-Plex-Container-Start=${start}&X-Plex-Container-Size=100`)).MediaContainer;
    if (!c || !Number.isSafeInteger(c.totalSize) || c.totalSize < 0 || c.totalSize > 100000 ||
        (total !== undefined && total !== c.totalSize) || (c.offset ?? 0) !== start) fail();
    total = c.totalSize; const batch = array(c.Metadata ?? []);
    if (batch.length > 100 || c.size !== batch.length) fail(); rows.push(...batch);
    if (rows.length === total) break;
    if (rows.length > total || batch.length !== 100) fail();
  }
  if (rows.length !== total || new Set(rows.map(x => requiredId(x.ratingKey))).size !== total) fail();
  const matches = [];
  for (const row of rows) {
    const ids = array(row.Guid).map(g => g.id);
    const tmdbs = ids.filter(x => /^tmdb:\/\/\d+$/.test(x)).map(x => requiredId(x.slice(7)));
    const imdbs = ids.filter(x => /^imdb:\/\/tt\d+$/.test(x)).map(x => imdbId(x.slice(7)));
    if (!tmdbs.length && !imdbs.length) fail(); // Unknown identity cannot establish absence.
    if (tmdbs.includes(tmdb) || (imdb && imdbs.includes(imdb))) {
      if (tmdbs.some(x => x !== tmdb) || (imdb && imdbs.some(x => x !== imdb))) fail();
      matches.push(row);
    }
  }
  if (matches.length > 1) fail();
  if (!matches.length) return false;
  const key = requiredId(matches[0].ratingKey);
  const detail = array((await client.call(`library/metadata/${key}?checkFiles=1&includeGuids=1`)).MediaContainer?.Metadata);
  if (detail.length !== 1 || requiredId(detail[0].ratingKey) !== key || String(detail[0].librarySectionID) !== library) fail();
  const parts = array(detail[0].Media).flatMap(m => array(m.Part));
  // Only explicit non-existence is sufficient. Missing/inaccessible flags block.
  return !parts.length || parts.some(p => p.exists !== false && p.exists !== 0 && p.exists !== '0');
}
