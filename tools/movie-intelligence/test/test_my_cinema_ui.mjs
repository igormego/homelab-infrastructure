import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, chmodSync, readdirSync, statSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createMyCinemaUi } from '../my-cinema-ui.mjs';

const credentials='Basic '+Buffer.from('owner:password-for-isolated-test').toString('base64');
const origin='http://127.0.0.1:1';
async function fixture(options={}) {
  const dir=mkdtempSync(join(tmpdir(),'cinema-ui-'));chmodSync(dir,0o700);
  const state={calls:0,plans:0,plan:{decision:'reacquirable',fingerprint:'a'.repeat(64),reason:'preflight_passed'}};
  const app=createMyCinemaUi({
    catalog:async()=>options.catalog??[{tmdb_id:123,title:'A & <B>',year:2024}],
    plan:async()=>{state.plans++; return options.plan ? options.plan(state):state.plan},
    execute:async()=>{state.calls++;if(options.failExecute) throw Error('unsafe upstream body');return {decision:options.outcome??'submitted'};},
    username:'owner',password:'password-for-isolated-test',secret:'test-secret-for-signing-32-characters-at-least',
    origin,receiptDirectory:dir,now:()=>1700000000000,
  });
  await new Promise(resolve=>app.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+app.address().port;
  const fetchPage=async(path,opts={})=>fetch(base+path,{
    ...opts,headers:{authorization:credentials,...opts.headers},redirect:'manual',
  });
  const close=async()=>{await new Promise(resolve=>app.close(resolve));rmSync(dir,{recursive:true,force:true});};
  return {state,dir,app,base,fetchPage,close};
}
function fields(page) {
  const get=k=>{const m=page.match(new RegExp('name="'+k+'" value="([^"]+)"'));assert.ok(m,k);return m[1];};
  return new URLSearchParams(Object.fromEntries(['expiry','lane','fingerprint','token'].map(k=>[k,get(k)])));
}
async function submit(f,form,extra={}) {return f.fetchPage('/movie/123/request',{
  method:'POST',headers:{'content-type':'application/x-www-form-urlencoded',origin,...extra.headers},
  body:form.toString(),...extra,
});}

test('authentication is required before any catalog read',async t=>{
 const f=await fixture();t.after(f.close);
 const r=await fetch(f.base+'/');assert.equal(r.status,401);assert.equal(r.headers.get('cache-control'),'no-store');
 assert.match(r.headers.get('www-authenticate'),/Basic/);
 const body=await r.text();assert.ok(!body.includes('A &'));
});
test('archive catalog escapes untrusted Plex text and does not include scripts',async t=>{
 const f=await fixture();t.after(f.close);
 const r=await f.fetchPage('/');assert.equal(r.status,200);
 const s=await r.text();assert.match(s,/A &amp; &lt;B&gt;/);assert.ok(!s.includes('A & <B>'));
 assert.match(r.headers.get('content-security-policy'),/default-src 'none'/);
});
test('invalid origin, missing token and cross-site requests cannot execute',async t=>{
 const f=await fixture();t.after(f.close);
 const form=fields(await (await f.fetchPage('/movie/123')).text());
 assert.equal((await submit(f,form,{headers:{origin:'https://evil.example'}})).status,403);
 const bad=new URLSearchParams(form);bad.set('token','0'.repeat(64));
 assert.equal((await submit(f,bad)).status,403);
 assert.equal((await submit(f,form,{headers:{origin,'sec-fetch-site':'cross-site'}})).status,403);
 assert.equal(f.state.calls,0);assert.deepEqual(readdirSync(f.dir),[]);
});
test('single explicit POST invokes guarded action once and journals replay',async t=>{
 const f=await fixture();t.after(f.close);
 const form=fields(await (await f.fetchPage('/movie/123')).text());
 const r=await submit(f,form);assert.equal(r.status,200);assert.match(await r.text(),/submitted/);
 assert.equal(f.state.calls,1);
 assert.equal((await submit(f,form)).status,409);assert.equal(f.state.calls,1);
 const files=readdirSync(f.dir);assert.equal(files.length,1);
 assert.equal(statSync(join(f.dir,files[0])).mode&0o777,0o600);
 const payload=readFileSync(join(f.dir,files[0]),'utf8');assert.match(payload,/"stage":"reserved"/);
 assert.match(payload,/"stage":"result_submitted"/);assert.ok(!payload.includes(credentials));
});
test('replanning enforces reviewed fingerprint and lane',async t=>{
 const f=await fixture({plan:state=>state.plans>1?{decision:'requestable',fingerprint:'b'.repeat(64)}:state.plan});
 t.after(f.close);
 const form=fields(await (await f.fetchPage('/movie/123')).text());
 assert.equal((await submit(f,form)).status,409);
 assert.equal(f.state.calls,0);assert.deepEqual(readdirSync(f.dir),[]);
});
test('requestable lane uses same explicit POST protections',async t=>{
 const f=await fixture({plan:()=>({decision:'requestable',fingerprint:'c'.repeat(64),reason:'ready'}),outcome:'requested'});
 t.after(f.close);
 const form=fields(await (await f.fetchPage('/movie/123')).text());
 assert.equal(form.get('lane'),'requestable');
 assert.equal((await submit(f,form)).status,200);assert.equal(f.state.calls,1);
});
test('unknown upstream result retains durable reservation and does not retry',async t=>{
 const f=await fixture({failExecute:true});t.after(f.close);
 const form=fields(await (await f.fetchPage('/movie/123')).text());
 assert.equal((await submit(f,form)).status,503);
 assert.equal((await submit(f,form)).status,409);
 assert.equal(f.state.calls,1);assert.match(readFileSync(join(f.dir,readdirSync(f.dir)[0]),'utf8'),/uncertain/);
});
test('nonarchived identities and read-only blocked plans never expose action',async t=>{
 const f=await fixture({plan:()=>({decision:'blocked',reason:'already_available'})});t.after(f.close);
 assert.equal((await f.fetchPage('/movie/987')).status,404);
 const s=await(await f.fetchPage('/movie/123')).text();
 assert.ok(!s.includes('<form'));assert.match(s,/already_available/);
 assert.equal((await submit(f,new URLSearchParams())).status,403);
});
test('unsafe config is refused before server creation',()=>{
 const dir=mkdtempSync(join(tmpdir(),'cinema-ui-bad-'));
 try {chmodSync(dir,0o777);
  assert.throws(()=>createMyCinemaUi({
    catalog:async()=>[],plan:async()=>({}),execute:async()=>{},
    username:'x',password:'password-for-isolated-test',
    secret:'test-secret-for-signing-32-characters-at-least',receiptDirectory:dir,origin,
  }),/unsafe_receipt_directory/);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
