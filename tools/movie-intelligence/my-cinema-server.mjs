/** Explicit host-only runtime wiring for the existing guarded movie controllers.
 * Start only after private credentials, UI journal and review are provisioned.
 * Binds loopback; any external exposure requires a separately reviewed auth/TLS proxy.
 */
import { createHash } from 'node:crypto';
import { createMyCinemaUi } from './my-cinema-ui.mjs';
import { createMovieRequestController } from './seerr-request.mjs';
import { createMovieReacquisitionController } from './reacquire.mjs';
import { readMovieIntelligence } from './sqlite.mjs';
import { readNormalizedMovieIntelligence } from './sqlite-normalized.mjs';
import { readRadarr } from './radarr.mjs';
import { jsonClient } from './reacquire-http.mjs';

const required = name => {
  const value = process.env[name];
  if (!value) throw Error('missing_private_config_' + name);
  return value;
};
const portText = process.env.MY_CINEMA_PORT ?? '8733';
if (!/^[1-9][0-9]{0,4}$/.test(portText) || Number(portText)>65535) throw Error('invalid_port');
const port = Number(portText);
const moviesLibraryId = Number(required('PLEX_MOVIES_LIBRARY_ID'));
const archiveLibraryId = Number(required('PLEX_ARCHIVE_LIBRARY_ID'));
if (!Number.isSafeInteger(moviesLibraryId) || !Number.isSafeInteger(archiveLibraryId) ||
    moviesLibraryId<1 || archiveLibraryId<1 || moviesLibraryId===archiveLibraryId) throw Error('invalid_library_scope');
const radarr={baseUrl:required('RADARR_BASE_URL'),apiKey:required('RADARR_API_KEY')};
const seerr={baseUrl:required('SEERR_BASE_URL'),apiKey:required('SEERR_API_KEY')};
const plex={baseUrl:required('PLEX_BASE_URL'),apiKey:required('PLEX_TOKEN'),moviesLibraryId};
const dbPath=required('MOVIE_DB_PATH');
const movieIntelligence=()=>readNormalizedMovieIntelligence({dbPath});
const request=createMovieRequestController({
  radarr:()=>readRadarr(radarr), seerr,
  movieIntelligence:()=>readMovieIntelligence({dbPath,schemaMode:'normalized'}),
});
const reacquire=createMovieReacquisitionController({
  radarr,seerr,plex,movieIntelligence,
  journalDirectory:required('REACQUIRE_JOURNAL_DIR'),
});
const plexRead=jsonClient(plex,'','X-Plex-Token');
async function catalog() {
  const sections=(await plexRead.call('library/sections')).MediaContainer?.Directory;
  const selected=sections?.filter(s=>String(s.key)===String(archiveLibraryId));
  if (selected?.length!==1 || selected[0].type!=='movie' ||
      !selected[0].title.startsWith('Watched')) throw Error('unexpected_archive_library');
  const movies=[];
  let total;
  for(let start=0;start<10000;start+=100) {
    const c=(await plexRead.call('library/sections/'+archiveLibraryId+
      '/all?type=1&includeGuids=1&X-Plex-Container-Start='+start+'&X-Plex-Container-Size=100')).MediaContainer;
    if(!c || !Number.isSafeInteger(c.totalSize) || c.totalSize>10000 ||
       (total!==undefined&&total!==c.totalSize) || (c.offset??0)!==start ||
       !Array.isArray(c.Metadata??[]) || c.size!==(c.Metadata??[]).length) throw Error('bad_archive_listing');
    total=c.totalSize;
    for(const m of c.Metadata??[]) {
      const ids=(m.Guid??[]).map(g=>g.id).filter(g=>/^tmdb:\/\/[1-9][0-9]*$/.test(g));
      if(ids.length!==1) throw Error('archive_identity_unavailable');
      movies.push({tmdb_id:Number(ids[0].slice(7)),title:m.title,year:m.year});
    }
    if(movies.length===total) return movies;
    if(movies.length>total || (c.Metadata??[]).length!==100) throw Error('incomplete_archive_listing');
  }
  throw Error('archive_listing_limit');
}
const hashPlan = result => createHash('sha256').update(JSON.stringify({
  tmdb_id:result.identity?.tmdb_id,decision:result.decision,
  evidence:result.evidence,reason:result.reason,
})).digest('hex');
async function plan(tmdb) {
  // The managed-no-file lane is distinct from Seerr's existing already_managed guard.
  const r=await reacquire.plan_reacquire_movie({tmdb_id:tmdb});
  if(r.decision==='reacquirable')
    return {decision:r.decision,fingerprint:r.plan_fingerprint,reason:r.reason};
  const q=await request.plan_movie_request({tmdb_id:tmdb});
  if(q.decision==='requestable')
    return {decision:q.decision,fingerprint:hashPlan(q),reason:q.reason};
  return {decision:'blocked',reason:r.reason??q.reason??'unavailable'};
}
async function execute(tmdb,p) {
  if(p.decision==='reacquirable') return reacquire.reacquire_movie({tmdb_id:tmdb},{
    execute:true,plan_fingerprint:p.fingerprint,
  });
  if(p.decision==='requestable') return request.request_movie({tmdb_id:tmdb},{execute:true});
  throw Error('invalid_action');
}
const app=createMyCinemaUi({
  catalog,plan,execute,
  username:required('MY_CINEMA_UI_USER'),password:required('MY_CINEMA_UI_PASSWORD'),
  secret:required('MY_CINEMA_UI_SIGNING_SECRET'),
  receiptDirectory:required('MY_CINEMA_UI_JOURNAL_DIR'),
  origin:'http://127.0.0.1:'+port,
});
// Deliberately no hostname/bind override. Public/LAN ingress is a separate change.
app.listen(port,'127.0.0.1',()=>process.stdout.write('my_cinema_ui_listening_loopback\n'));
