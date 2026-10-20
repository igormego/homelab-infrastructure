import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { imdbId, radarrRecord } from './model.mjs';
import { get_movie_status } from './get_movie_status.mjs';
import { readSeerrReacquisitionSafety } from './seerr-request.mjs';
import { jsonClient, requiredId, array, radarrQueue, plexConflict } from './reacquire-http.mjs';
import { reacquireJournal } from './reacquire-journal.mjs';

const hash = x => createHash('sha256').update(JSON.stringify(x)).digest('hex');
const preserved = movie => { const copy = structuredClone(movie); delete copy.monitored; return copy; };
const result = (decision, reason, extra = {}) => ({ decision, reason, mutation_attempted: false, ...extra });
const commandMatches = (r, movieId) => r.name === 'MoviesSearch' &&
  Array.isArray(r.body?.movieIds) && r.body.movieIds.length === 1 && r.body.movieIds[0] === movieId;

/** Trusted deployment configuration only. User input cannot supply URLs, adapters or policies. */
export function createMovieReacquisitionController({ radarr, seerr, plex, movieIntelligence, journalDirectory }) {
  let busy = false;
  async function inspect(input, allowMonitored = false) {
    if (!input || Array.isArray(input) || Object.keys(input).some(k => !['tmdb_id', 'imdb_id', 'expected_quality_profile_id', 'expected_root_folder'].includes(k)))
      return { public: result('blocked', 'invalid_input') };
    const tmdb = requiredId(input.tmdb_id); const requestedImdb = imdbId(input.imdb_id);
    const client = jsonClient(radarr, 'api/v3/');
    if ((await client.call('system/status')).version !== '6.1.1.10360') return { public: result('blocked', 'unreviewed_radarr_version') };
    const movies = array(await client.call('movie'));
    const candidates = movies.filter(m => m.tmdbId === tmdb || (requestedImdb && m.imdbId === requestedImdb));
    if (candidates.length !== 1) return { public: result('blocked', 'radarr_identity_not_unique') };
    const movie = candidates[0]; const movieId = requiredId(movie.id);
    const imdb = imdbId(movie.imdbId);
    if (movie.tmdbId !== tmdb || (requestedImdb && imdb && imdb !== requestedImdb)) return { public: result('blocked', 'identity_conflict') };
    if (movie.hasFile !== false || (movie.movieFileId ?? 0) !== 0 || (movie.movieFile?.id ?? 0) !== 0 || (movie.sizeOnDisk ?? 0) !== 0)
      return { public: result('blocked', 'file_present_or_unknown') };
    if (movie.monitored !== allowMonitored) return { public: result('blocked', 'monitored_state_conflict') };
    requiredId(movie.qualityProfileId);
    if (typeof movie.rootFolderPath !== 'string' || !movie.rootFolderPath || typeof movie.path !== 'string' || !movie.path ||
        typeof movie.minimumAvailability !== 'string' || !movie.minimumAvailability || !Array.isArray(movie.tags)) throw new Error();
    movie.tags.forEach(requiredId);
    if ((input.expected_quality_profile_id !== undefined && requiredId(input.expected_quality_profile_id) !== movie.qualityProfileId) ||
        (input.expected_root_folder !== undefined && input.expected_root_folder !== movie.rootFolderPath))
      return { public: result('blocked', 'profile_or_root_conflict') };
    const profiles = array(await client.call('qualityprofile'));
    const roots = array(await client.call('rootfolder'));
    const profile = profiles.filter(p => p.id === movie.qualityProfileId);
    if (profile.length !== 1 || roots.filter(r => r.path === movie.rootFolderPath && r.accessible === true).length !== 1)
      return { public: result('blocked', 'profile_or_root_unavailable') };
    const identity = await get_movie_status({ tmdb_id: tmdb, ...(imdb ? { imdb_id: imdb } : {}) }, {
      movieIntelligence, radarr: () => movies.map(m => radarrRecord(m, new Map(profiles.map(p => [p.id, p.name])))),
    });
    if (!['confirmed_match', 'single_source_only'].includes(identity.summary.state)) return { public: result('blocked', 'canonical_identity_conflict') };
    const safety = await readSeerrReacquisitionSafety(seerr, tmdb);
    const knownImdbs = [imdb, requestedImdb, safety.movie.imdb_id,
      ...identity.sources.movie_intelligence.candidates.map(m => m.external_ids.imdb)].filter(Boolean);
    if (new Set(knownImdbs).size > 1)
      return { public: result('blocked', 'seerr_identity_conflict') };
    if (safety.requests.length || safety.movie.requests.length || safety.movie.status !== 1 || safety.movie.status4k !== 1)
      return { public: result('blocked', 'seerr_request_or_availability_conflict') };
    const queue = await radarrQueue(client);
    if (queue.some(q => !q.movieId || requiredId(q.movieId) === movieId)) return { public: result('blocked', 'active_or_unidentified_queue') };
    // Fail conservatively while any Radarr command is queued/running, including a
    // bulk search/import that has not yet produced a download queue entry.
    const commands = array(await client.call('command'));
    if (commands.some(c => !['completed', 'failed', 'aborted', 'cancelled', 'orphaned'].includes(c.status)))
      return { public: result('blocked', 'active_command') };
    if (await plexConflict(plex, tmdb, imdb ?? safety.movie.imdb_id)) return { public: result('blocked', 'plex_available_or_unknown') };
    const operations = [
      { method: 'PUT', endpoint: '/api/v3/movie/editor', body: { movieIds: [movieId], monitored: true } },
      { method: 'POST', endpoint: '/api/v3/command', body: { name: 'MoviesSearch', movieIds: [movieId] } },
    ];
    const guard = { movie: preserved(movie), profile };
    return { client, movie, guard, public: result('reacquirable', 'preflight_passed', {
      identity: { tmdb_id: tmdb, imdb_id: imdb ?? safety.movie.imdb_id, radarr_id: movieId, title: safety.movie.title },
      plan_fingerprint: hash(guard), operations,
      changes: { monitored: { before: false, after: true }, search_commands: 1 },
      preserve: { quality_profile_id: movie.qualityProfileId, root_folder: movie.rootFolderPath,
        path: movie.path, minimum_availability: movie.minimumAvailability, tags: movie.tags, other_movie_fields: 'unchanged', quality_profile: 'unchanged' },
      rollback: 'No automatic rollback. Unmonitoring cannot undo a search, grab or download; reconcile queue/history and receipt before any separately approved reversal.',
    }) };
  }
  async function plan_reacquire_movie(input) {
    try {
      const inspection = await inspect(input);
      if (inspection.client && journalDirectory) {
        const prior = reacquireJournal(journalDirectory, inspection.client.scope, inspection.public.identity.tmdb_id).read();
        if (prior) return result(prior, 'durable_receipt_exists');
      }
      return inspection.public;
    } catch { return result('blocked', 'preflight_unavailable'); }
  }
  async function reacquire_movie(input, options = {}) {
    if (!options || Array.isArray(options) || Object.keys(options).some(k => !['execute', 'plan_fingerprint'].includes(k)) ||
        (options.execute !== undefined && typeof options.execute !== 'boolean')) return result('blocked', 'invalid_options');
    if (options.execute !== true) return plan_reacquire_movie(input);
    if (busy) return result('blocked', 'execution_in_progress');
    busy = true; let attempted = false; let receipt;
    try {
      // Check durable receipt before network preflight: replay remains blocked
      // even if a prior write changed monitored/file state or dependencies are down.
      const tmdb = requiredId(input?.tmdb_id); const client = jsonClient(radarr, 'api/v3/');
      receipt = reacquireJournal(journalDirectory, client.scope, tmdb);
      const prior = receipt.read(); if (prior) return result(prior, 'durable_receipt_exists');
      const first = await inspect(input);
      if (first.public.decision !== 'reacquirable') return first.public;
      if (options.plan_fingerprint !== first.public.plan_fingerprint) return result('blocked', 'reviewed_plan_required_or_changed');
      receipt.reserve({ tmdb_id: tmdb, radarr_id: first.movie.id, fingerprint: options.plan_fingerprint, at: new Date().toISOString() });
      const fresh = await inspect(input);
      if (fresh.public.decision !== 'reacquirable' || !isDeepStrictEqual(first.guard, fresh.guard))
        return result('blocked', 'reserved_preflight_changed_requires_review');
      receipt.append({ stage: 'monitor_attempt', at: new Date().toISOString() }); attempted = true;
      const update = array(await client.call('movie/editor', 'PUT', { movieIds: [first.movie.id], monitored: true }));
      if (update.length !== 1 || update[0].id !== first.movie.id) throw new Error();
      // Re-read every guard after monitoring. Unexpected drift stops BEFORE search.
      const after = await inspect(input, true);
      if (after.public.decision !== 'reacquirable' || !isDeepStrictEqual(first.guard, after.guard)) throw new Error();
      receipt.append({ stage: 'search_attempt', at: new Date().toISOString() });
      const command = await client.call('command', 'POST', { name: 'MoviesSearch', movieIds: [first.movie.id] });
      const commandId = requiredId(command.id);
      if (!commandMatches(command, first.movie.id)) throw new Error();
      const readback = await client.call(`command/${commandId}`);
      if (readback.id !== commandId || !commandMatches(readback, first.movie.id) ||
          !['queued', 'started', 'completed'].includes(readback.status)) throw new Error();
      receipt.append({ stage: 'submitted', command_id: commandId, at: new Date().toISOString() });
      return result('submitted', 'single_search_accepted_not_download_acceptance', { mutation_attempted: true, identity: first.public.identity, command_id: commandId });
    } catch {
      // Any reservation (including partial/corrupt receipt) is retained forever.
      return result(attempted ? 'uncertain_requires_review' : 'blocked', attempted ? 'write_or_verification_uncertain_no_retry' : 'preflight_or_reservation_failed', { mutation_attempted: attempted });
    } finally { busy = false; }
  }
  return { plan_reacquire_movie, reacquire_movie };
}
