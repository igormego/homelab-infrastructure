/** Fixed read-only entry point for the existing Seerr/Movie Intelligence planner.
 * No --execute option; no exported or reachable movie-request mutation handler.
 * This CLI is a trusted local helper, not an arbitrary remote command endpoint.
 */
import { parseArgs } from 'node:util';
import { readFileSync } from 'node:fs';
import { readMovieIntelligence } from './sqlite.mjs';
import { readRadarr } from './radarr.mjs';
import { createMovieRequestController } from './seerr-request.mjs';

const reject = (reason = 'invalid_arguments') => ({
  contract_version: '0.1', decision: 'blocked', reason,
  identity: null, evidence: null, mutation_attempted: false,
});
const allowedDecisions = new Set([
  'requestable', 'already_available', 'already_requested', 'already_managed',
  'ambiguous', 'blocked', 'unresolved',
]);
const fixedReasons = new Set([
  'invalid_input', 'title_requires_year', 'unsupported_version_or_visibility',
  'multiple_search_candidates', 'no_unique_movie', 'resolved_identity_conflict',
  'read_identity_or_source_unresolved', 'source_identity_conflict',
  'seerr_blocklisted', 'file_or_seerr_available', 'existing_request_or_processing',
  'failed_or_declined_request_requires_review', 'radarr_manages_movie',
  'media_policy_dependency', 'stable_identity_and_clear_preflight',
  'source_or_configuration_unavailable',
]);

function parseSelector(argv) {
  const { values } = parseArgs({
    args: argv, strict: true, allowPositionals: false,
    options: {
      title: { type: 'string' }, year: { type: 'string' },
      'tmdb-id': { type: 'string' },
    },
  });
  if (Object.keys(values).length === 1 && values['tmdb-id'] !== undefined) {
    if (!/^[1-9][0-9]{0,15}$/.test(values['tmdb-id'])) throw Error('invalid');
    const tmdb = Number(values['tmdb-id']);
    if (!Number.isSafeInteger(tmdb)) throw Error('invalid');
    return { tmdb_id: tmdb };
  }
  if (Object.keys(values).length === 2 && values.title !== undefined && values.year !== undefined) {
    if (values.title !== values.title.trim() || !values.title || [...values.title].length > 200 ||
        Buffer.byteLength(values.title, 'utf8') > 512 || /\p{C}/u.test(values.title) ||
        !/^[0-9]{4}$/.test(values.year)) throw Error('invalid');
    const year = Number(values.year);
    if (year < 1888 || year > 2100) throw Error('invalid');
    return { title: values.title, year };
  }
  throw Error('invalid');
}

function boundedPlan(result) {
  if (!result || result.contract_version !== '0.1' || !allowedDecisions.has(result.decision) ||
      !fixedReasons.has(result.reason) || result.audit?.mutation_attempted !== false) {
    return reject('source_unavailable');
  }
  const m = result.identity;
  if (m !== null && (!Number.isSafeInteger(m?.tmdb_id) || m.tmdb_id <= 0 ||
      typeof m.title !== 'string' || m.title.length > 500 ||
      (m.year !== null && (!Number.isInteger(m.year) || m.year < 1888 || m.year > 2100)))) {
    return reject('source_unavailable');
  }
  if (result.decision === 'requestable' && m === null) return reject('source_unavailable');
  const e = result.evidence;
  let evidence = null;
  if (e !== null) {
    const src = (v) => v && typeof v.status === 'string' && v.status.length <= 40 &&
      Number.isSafeInteger(v.count) && v.count >= 0 && v.count <= 1_000_000
      ? { status: v.status, count: v.count } : null;
    const mi = src(e.movie_intelligence), radarr = src(e.radarr);
    if (!mi || !radarr || typeof e.read_state !== 'string' || e.read_state.length > 40 ||
        !Number.isInteger(e.seerr_status) || e.seerr_status < 1 || e.seerr_status > 7) {
      return reject('source_unavailable');
    }
    evidence = { read_state: e.read_state, movie_intelligence: mi, radarr, seerr_status: e.seerr_status };
  }
  return {
    contract_version: '0.1', decision: result.decision, reason: result.reason,
    identity: m ? { tmdb_id: m.tmdb_id, title: m.title, year: m.year } : null,
    evidence, mutation_attempted: false,
  };
}

try {
  const selector = parseSelector(process.argv.slice(2));
  const controller = createMovieRequestController({
    seerr: { baseUrl: process.env.SEERR_BASE_URL, apiKey: process.env.SEERR_API_KEY },
    movieIntelligence: () => readMovieIntelligence({
      dbPath: process.env.MOVIE_DB_PATH,
      schemaMode: 'normalized',
    }),
    radarr: () => readRadarr({
      baseUrl: process.env.RADARR_BASE_URL, apiKey: process.env.RADARR_API_KEY,
    }),
  });
  // This entry point can ONLY call the read-only plan method.
  const plan = boundedPlan(await controller.plan_movie_request(selector));
  process.stdout.write(JSON.stringify(plan) + '\n');
  if (plan.decision === 'blocked' && plan.reason === 'source_unavailable') process.exitCode = 2;
} catch {
  process.stdout.write(JSON.stringify(reject()) + '\n');
  process.exitCode = 2;
}
