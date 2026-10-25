// Observation only. The optional smaller-memory experiment is a disclosed build
// transform in run.mjs, never a mutation of this constructor or engine binary.
const NativeMemory = WebAssembly.Memory;
const memories = [];
WebAssembly.Memory = new Proxy(NativeMemory, {
  construct(target, args) {
    const memory = Reflect.construct(target, args);
    memories.push({ memory, descriptor: { ...args[0] }, initial_buffer_bytes: memory.buffer.byteLength });
    return memory;
  },
});

let engine;
let api;
let created;
let openMs;
let sqliteVersion;
const filename = 'memory-audit-disposable.db';
const hex = bytes => Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');

async function open(kind) {
  engine = kind;
  const began = performance.now();
  if (kind === 'empty') { openMs = performance.now() - began; return state(); }
  if (kind === 'turso') {
    const { connect } = await import('@tursodatabase/database-wasm');
    const db = await connect(filename);
    api = {
      exec: sql => db.exec(sql),
      run: async (sql, values = []) => (await db.prepare(sql)).run(...values),
      all: async (sql, values = []) => (await db.prepare(sql)).all(...values),
      close: () => db.close(),
    };
  } else if (kind === 'sqlite') {
    const { default: initialize } = await import('@sqlite.org/sqlite-wasm');
    const sqlite3 = await initialize();
    if (!sqlite3.oo1.OpfsDb) throw new Error('Official SQLite OPFS is unavailable; memory fallback is forbidden.');
    sqliteVersion = sqlite3.version.libVersion;
    const db = new sqlite3.oo1.OpfsDb('/' + filename, 'c');
    api = {
      exec: sql => db.exec(sql),
      run: (sql, values = []) => db.exec({ sql, bind: values }),
      all: (sql, values = []) => db.exec({ sql, bind: values, rowMode: 'object', returnValue: 'resultRows' }),
      close: () => db.close(),
    };
  } else throw new Error('Unknown engine.');
  await api.exec('PRAGMA page_size = 4096; PRAGMA cache_size = -2000; PRAGMA locking_mode = EXCLUSIVE; PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL;');
  await api.exec(`
    CREATE TABLE IF NOT EXISTS identity (slot INTEGER PRIMARY KEY, seed BLOB NOT NULL) STRICT;
    CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL) STRICT;
    CREATE TABLE IF NOT EXISTS payloads (id INTEGER PRIMARY KEY, data BLOB NOT NULL) STRICT;
  `);
  const existing = await api.all('SELECT seed FROM identity WHERE slot = 1');
  created = existing.length === 0;
  if (created) {
    await api.exec('BEGIN');
    await api.run('INSERT INTO identity VALUES(1,?)', [crypto.getRandomValues(new Uint8Array(32))]);
    await api.run('INSERT INTO notes VALUES(1,?,1)', ['The same durable sample note.']);
    await api.exec('COMMIT');
  }
  openMs = performance.now() - began;
  return state();
}

async function state() {
  const snapshot = {
    engine, sqlite_version: sqliteVersion ?? null,
    open_ms: Math.round(openMs ?? 0),
    memories: memories.map(({ memory, descriptor, initial_buffer_bytes }) => ({
      descriptor, initial_buffer_bytes, current_buffer_bytes: memory.buffer.byteLength,
      shared: memory.buffer instanceof SharedArrayBuffer,
    })),
  };
  if (!api) return snapshot;
  const seed = (await api.all('SELECT seed FROM identity WHERE slot = 1'))[0].seed;
  return {
    ...snapshot,
    created_identity: created,
    identity_fingerprint: hex(new Uint8Array(await crypto.subtle.digest('SHA-256', seed))),
    sample: (await api.all('SELECT * FROM notes WHERE id = 1'))[0],
    note_count: (await api.all('SELECT COUNT(*) AS n FROM notes'))[0].n,
    payload_sizes: await api.all('SELECT id,length(data) AS bytes FROM payloads ORDER BY id'),
    pragmas: {
      journal_mode: await api.all('PRAGMA journal_mode'),
      synchronous: await api.all('PRAGMA synchronous'),
      page_size: await api.all('PRAGMA page_size'),
      cache_size: await api.all('PRAGMA cache_size'),
      locking_mode: await api.all('PRAGMA locking_mode'),
    },
  };
}

async function notes({ count, size }) {
  const body = 'n'.repeat(size);
  await api.exec('BEGIN');
  for (let index = 0; index < count; index++) {
    await api.run('INSERT INTO notes(id,body,revision) VALUES(?,?,1)', [index + 2, body]);
  }
  await api.run('UPDATE notes SET body=?,revision=revision+1 WHERE id=1', ['Updated durable sample after multiple writes.']);
  await api.exec('COMMIT');
  return state();
}

async function blob(size) {
  const data = new Uint8Array(size);
  data.fill(0x5a);
  await api.run('INSERT INTO payloads(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', [data]);
  const rows = await api.all('SELECT length(data) AS bytes,hex(substr(data,1,4)) AS first,hex(substr(data,-4)) AS last FROM payloads WHERE id=1');
  if (rows[0].bytes !== size || rows[0].first !== '5A5A5A5A' || rows[0].last !== '5A5A5A5A') throw new Error('Stored stress payload failed verification.');
  return state();
}

async function strict() {
  let rejected = false;
  try { await api.run('UPDATE notes SET revision=? WHERE id=1', ['not an integer']); }
  catch { rejected = true; }
  if (!rejected) throw new Error('STRICT typing failed.');
  return { rejected: true };
}

self.onmessage = ({ data }) => {
  const actions = { open, state, notes, blob, strict, close: () => api?.close() };
  Promise.resolve().then(() => actions[data.method](data.value)).then(
    value => self.postMessage({ id: data.id, value }),
    error => self.postMessage({ id: data.id, error: String(error) }),
  );
};
