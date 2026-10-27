import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../plan-cli.mjs', import.meta.url));

function run(args) {
  const env = { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '' };
  const r = spawnSync(process.execPath, [cli, ...args], {
    encoding: 'utf8', timeout: 8000, env, maxBuffer: 8192,
  });
  assert.equal(r.signal, null);
  assert.equal(r.stderr, '');
  assert.ok(r.stdout.length < 2000);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.contract_version, '0.1');
  assert.equal(payload.mutation_attempted, false);
  assert.equal(Object.hasOwn(payload, 'request'), false);
  assert.equal(Object.hasOwn(payload, 'intended_action'), false);
  return { ...r, payload };
}

test('plan-only CLI rejects execution switches and arbitrary transport fields', () => {
  for (const args of [
    ['--tmdb-id', '244786', '--execute'],
    ['--title', 'Whiplash', '--year', '2014', '--execute'],
    ['--tmdb-id', '244786', '--api-key', 'secret'],
    ['--tmdb-id', '244786', '--url', 'https://example.com'],
    ['--tmdb-id', '244786', '--profile', '9'],
    ['--tmdb-id', '244786', '--command', 'rm'],
  ]) {
    const r = run(args);
    assert.equal(r.status, 2);
    assert.deepEqual(r.payload, {
      contract_version: '0.1', decision: 'blocked', reason: 'invalid_arguments',
      identity: null, evidence: null, mutation_attempted: false,
    });
  }
});

test('plan-only CLI rejects invalid selector shapes before any credentials', () => {
  for (const args of [
    [], ['--tmdb-id', '0'], ['--tmdb-id', '01'], ['--tmdb-id', 'abc'],
    ['--tmdb-id', '9007199254740992'],
    ['--title', 'Whiplash'], ['--year', '2014'],
    ['--title', ' Whiplash', '--year', '2014'],
    ['--title', 'Whiplash', '--year', '2101'],
    ['--tmdb-id', '244786', '--title', 'Whiplash', '--year', '2014'],
  ]) {
    const r = run(args);
    assert.equal(r.status, 2);
    assert.equal(r.payload.reason, 'invalid_arguments');
  }
});

test('valid movie plan with absent credentials is non-executing and fails closed', () => {
  const r = run(['--tmdb-id', '244786']);
  assert.equal(r.status, 0);
  assert.equal(r.payload.decision, 'unresolved');
  assert.equal(r.payload.reason, 'source_or_configuration_unavailable');
  assert.equal(r.payload.identity, null);
  assert.equal(r.payload.evidence, null);
});
