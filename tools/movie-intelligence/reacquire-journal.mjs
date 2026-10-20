import { openSync, closeSync, writeSync, fsyncSync, readFileSync, lstatSync, realpathSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

// One durable, never-expiring reservation per server/TMDb identity. A crash or
// uncertain HTTP result always requires human reconciliation, never an auto-retry.
export function reacquireJournal(directory, scope, tmdb) {
  if (typeof directory !== 'string' || !isAbsolute(directory) || !/^[a-f0-9]{64}$/.test(scope) ||
      !Number.isSafeInteger(tmdb) || tmdb <= 0) throw new Error('journal_unavailable');
  const stat = lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('journal_unavailable');
  const root = realpathSync(directory);
  const path = join(root, `${scope}-${tmdb}.jsonl`);
  return {
    read() {
      try {
        const s = lstatSync(path);
        if (!s.isFile() || s.isSymbolicLink() || s.size > 65536) throw new Error('journal_unavailable');
        const lines = readFileSync(path, 'utf8').trim().split('\n').map(x => JSON.parse(x));
        return lines.at(-1)?.stage === 'submitted' ? 'already_executed' : 'uncertain_requires_review';
      } catch (e) { if (e.code === 'ENOENT') return null; throw new Error('journal_unavailable'); }
    },
    reserve(record) {
      let fd;
      try {
        fd = openSync(path, 'wx', 0o600);
        writeSync(fd, JSON.stringify({ ...record, stage: 'reserved' }) + '\n'); fsyncSync(fd);
        // Persist directory entry on POSIX before a network write; Windows uses
        // its filesystem durability semantics and the flushed file handle.
        if (process.platform !== 'win32') { const d = openSync(root, 'r'); try { fsyncSync(d); } finally { closeSync(d); } }
      } finally { if (fd !== undefined) closeSync(fd); }
    },
    append(record) {
      const fd = openSync(path, 'a');
      try { writeSync(fd, JSON.stringify(record) + '\n'); fsyncSync(fd); } finally { closeSync(fd); }
    },
  };
}
