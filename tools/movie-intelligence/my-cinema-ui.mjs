/** Owner-only, local-first My Cinema request screen. No executable shell or public API. */
import { createServer } from 'node:http';
import { createHmac, createHash, timingSafeEqual } from 'node:crypto';
import { openSync, writeSync, fsyncSync, closeSync, lstatSync, realpathSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';

const escape = s => String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
const numeric = s => /^[1-9][0-9]{0,15}$/.test(String(s)) && Number.isSafeInteger(Number(s)) ? Number(s) : null;
const sha = s => createHash('sha256').update(s).digest();
const equal = (a, b) => timingSafeEqual(sha(a), sha(b));
const html = (title, body) => '<!doctype html><html lang="sk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>body{background:#10151f;color:#e7edf8;font:17px system-ui;max-width:790px;margin:auto;padding:2rem}a{color:#9dc8fa}article{background:#202939;padding:1.2rem;margin:1rem 0;border-radius:12px}button{padding:.7rem 1.5rem;background:#75bcba;border:0;border-radius:7px;font-weight:700}small{color:#a8b3c5}</style><h1>' + escape(title) + '</h1>' + body + '</html>';

function privateDirectory(directory) {
  if (!isAbsolute(directory)) throw Error('private_receipt_directory_required');
  const s = lstatSync(directory);
  if (!s.isDirectory() || s.isSymbolicLink() || (s.mode & 0o077) !== 0) throw Error('unsafe_receipt_directory');
  return realpathSync(directory);
}
function reserve(directory, tmdb, lane, fingerprint) {
  const path = join(directory, 'ui-' + tmdb + '.jsonl');
  let fd;
  try {
    fd = openSync(path, 'wx', 0o600);
    writeSync(fd, JSON.stringify({ stage: 'reserved', tmdb_id: tmdb, lane,
      fingerprint_hash: createHash('sha256').update(fingerprint).digest('hex'),
      at: new Date().toISOString() }) + '\n');
    fsyncSync(fd);
    closeSync(fd); fd = undefined;
    if (process.platform !== 'win32') {
      const d = openSync(directory, 'r'); try { fsyncSync(d); } finally { closeSync(d); }
    }
    return path;
  } finally { if (fd !== undefined) closeSync(fd); }
}
function append(path, stage) {
  const fd = openSync(path, 'a');
  try { writeSync(fd, JSON.stringify({ stage, at: new Date().toISOString() }) + '\n'); fsyncSync(fd); }
  finally { closeSync(fd); }
}
function boundedEntries(entries) {
  if (!Array.isArray(entries) || entries.length > 10000) throw Error('catalog_unavailable');
  const seen = new Set();
  return entries.map(m => {
    const id = numeric(m.tmdb_id);
    if (!id || typeof m.title !== 'string' || !m.title.trim() || m.title.length > 300 ||
        !Number.isInteger(m.year) || m.year < 1888 || m.year > 2100 || seen.has(id)) throw Error('catalog_unavailable');
    seen.add(id);
    return { tmdb_id:id, title:m.title, year:m.year };
  });
}
async function bodyText(req) {
  const chunks = []; let total = 0;
  for await (const chunk of req) { total += chunk.length; if (total > 4096) throw Error('body_too_large'); chunks.push(chunk); }
  return Buffer.concat(chunks).toString('utf8');
}
/** plan(id) => {decision:'reacquirable'|'requestable'|..., fingerprint?, reason?};
 * execute(id, plan) delegates only to the two existing guarded controllers.
 * catalog() enumerates only the isolated Plex Watched library.
 * No plans are authorization: every POST replans and compares exact inputs.
 */
export function createMyCinemaUi({ catalog, plan, execute, username, password, secret,
  receiptDirectory, origin, now = () => Date.now() }) {
  if (typeof catalog !== 'function' || typeof plan !== 'function' || typeof execute !== 'function' ||
      typeof username !== 'string' || !username || typeof password !== 'string' || password.length < 16 ||
      typeof secret !== 'string' || secret.length < 32 || typeof origin !== 'string') throw Error('unsafe_ui_config');
  const expectedOrigin = new URL(origin);
  if (!['http:','https:'].includes(expectedOrigin.protocol) || expectedOrigin.pathname !== '/' ||
      expectedOrigin.search || expectedOrigin.hash || expectedOrigin.username || expectedOrigin.password) throw Error('unsafe_origin');
  const directory = privateDirectory(receiptDirectory);
  const authValue = 'Basic ' + Buffer.from(username + ':' + password).toString('base64');
  const sign = (tmdb, lane, fingerprint, expiry) => createHmac('sha256', secret)
    .update([String(tmdb),lane,fingerprint,String(expiry)].join('\n')).digest('hex');
  const validPlan = p => p && ['reacquirable','requestable'].includes(p.decision) &&
    typeof p.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(p.fingerprint);
  return createServer(async (req,res) => {
    res.setHeader('Cache-Control','no-store');
    res.setHeader('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
    res.setHeader('X-Frame-Options','DENY');
    res.setHeader('X-Content-Type-Options','nosniff');
    res.setHeader('Referrer-Policy','no-referrer');
    const send=(status,content)=>{res.writeHead(status,{'Content-Type':'text/html; charset=utf-8'});res.end(content);};
    if (typeof req.headers.authorization !== 'string' || req.headers.authorization.length > 1024 ||
        !equal(req.headers.authorization, authValue)) {
      res.setHeader('WWW-Authenticate','Basic realm="My Cinema"');
      return send(401,html('Prístup zamietnutý','<p>Vyžaduje sa prihlásenie.</p>'));
    }
    try {
      const u = new URL(req.url,'http://internal.invalid');
      const movie = /^\/movie\/([1-9][0-9]{0,15})$/.exec(u.pathname);
      const action = /^\/movie\/([1-9][0-9]{0,15})\/request$/.exec(u.pathname);
      if (req.method === 'GET' && u.pathname === '/') {
        const entries = boundedEntries(await catalog());
        return send(200,html('My Cinema',entries.map(m =>
          '<article><a href="/movie/' + m.tmdb_id + '">' + escape(m.title) +
          ' (' + m.year + ')</a></article>').join('') || '<p>Archív je prázdny.</p>'));
      }
      if ((req.method === 'GET' && movie) || (req.method === 'POST' && action)) {
        const tmdb = numeric((movie || action)[1]); if (!tmdb) return send(404,html('Nenájdené',''));
        const entry = boundedEntries(await catalog()).find(m=>m.tmdb_id===tmdb);
        if (!entry) return send(404,html('Nenájdené',''));
        const heading = entry.title + ' (' + entry.year + ')';
        if (req.method === 'GET') {
          const p = await plan(tmdb);
          if (!validPlan(p)) return send(200,html(heading,'<p>Momentálne nemožno požiadať o stiahnutie: ' +
            escape(p?.reason ?? 'unknown') + '</p><p><a href="/">Späť</a></p>'));
          const expiry = Math.floor(now()/1000)+600;
          return send(200,html(heading,'<p>Film je v historickom archíve. Potvrdenie môže spustiť Radarr vyhľadávanie a sťahovanie.</p>' +
            '<form method="post" action="/movie/' + tmdb + '/request"><input type="hidden" name="expiry" value="' + expiry +
            '"><input type="hidden" name="lane" value="' + escape(p.decision) +
            '"><input type="hidden" name="fingerprint" value="' + p.fingerprint +
            '"><input type="hidden" name="token" value="' + sign(tmdb,p.decision,p.fingerprint,expiry) +
            '"><button type="submit">Stiahnuť znova</button></form><p><small>Ak je požiadavka neistá, automaticky sa nezopakuje.</small></p>'));
        }
        const suppliedOrigin = req.headers.origin;
        if (suppliedOrigin && suppliedOrigin !== expectedOrigin.origin) return send(403,html('Zamietnuté',''));
        if (req.headers['sec-fetch-site'] && req.headers['sec-fetch-site'] !== 'same-origin' &&
            req.headers['sec-fetch-site'] !== 'none') return send(403,html('Zamietnuté',''));
        if (req.headers['content-type']?.split(';')[0] !== 'application/x-www-form-urlencoded') return send(415,html('Nepodporované',''));
        const form = new URLSearchParams(await bodyText(req));
        if ([...form].length !== 4 || [...form.keys()].some(k=>!['expiry','lane','fingerprint','token'].includes(k)) ||
            [...form.keys()].some(k=>form.getAll(k).length!==1)) return send(400,html('Neplatné údaje',''));
        const expiry = Number(form.get('expiry')), lane = form.get('lane'), fp = form.get('fingerprint');
        if (!Number.isSafeInteger(expiry) || expiry < Math.floor(now()/1000) ||
            expiry > Math.floor(now()/1000)+600 || !['reacquirable','requestable'].includes(lane) ||
            !/^[a-f0-9]{64}$/.test(fp ?? '') ||
            !equal(form.get('token') ?? '',sign(tmdb,lane,fp,expiry))) return send(403,html('Platnosť vypršala',''));
        const p = await plan(tmdb);
        if (!validPlan(p) || p.decision !== lane || p.fingerprint !== fp) return send(409,html('Stav sa zmenil','<p>Obnov detail filmu.</p>'));
        let receipt;
        try { receipt = reserve(directory,tmdb,lane,fp); }
        catch(e) { if (e.code === 'EEXIST') return send(409,html('Už spracované','<p>Predchádzajúcu požiadavku treba preveriť. Nič automaticky neopakujeme.</p>')); throw e; }
        let outcome;
        try { outcome = await execute(tmdb,p); append(receipt,'result_' + String(outcome?.decision ?? 'unknown').slice(0,40)); }
        catch { append(receipt,'uncertain'); return send(503,html('Neistý výsledok','<p>Žiadne automatické opakovanie. Skontroluj Radarr/Seerr.</p>')); }
        return send(['submitted','requested'].includes(outcome?.decision)?200:409,
          html(heading,'<p>Výsledok: ' + escape(outcome?.decision ?? 'unknown') + '</p><p><a href="/">Späť</a></p>'));
      }
      return send(404,html('Nenájdené',''));
    } catch { return send(503,html('Nedostupné','<p>Bezpečnostné overenie zlyhalo. Žiadna operácia sa nespustila, pokiaľ už nebola rezervovaná.</p>')); }
  });
}
