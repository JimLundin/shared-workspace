import { connect } from '@tursodatabase/database-wasm';
import { Domain } from '../../host-wasm/pkg/browser_workspace_host.js';
import { decodeSnapshot, object, string, number } from '../../../native/ui/src/protocol.ts';
import {
  batch, bytes, deviceName, fields, hex, json, parse, randomHex, requireThat, scope, signedOperations,
  type Batch, type Command, type Contact, type DataSnapshot, type ImportReceipt, type PeerRecord,
  type Receipt, type SignedOperation,
} from './types.ts';

type Database = Awaited<ReturnType<typeof connect>>;
type Invitation = Readonly<{ token_hash: string; workspace_ids: readonly string[]; expires_at_ms: number; accepted_peer: string | null }>;
const ZERO_IMPORT: ImportReceipt = { imported: 0, already_present: 0 };

/** The only SQL owner. Its queue never awaits any network operation. */
export class BrowserStore {
  readonly seedHex: string;
  readonly name: string;
  readonly id: string;
  readonly #db: Database;
  readonly #domain: Domain;
  readonly #peers = new Map<string, PeerRecord>();
  readonly #invitations = new Map<string, Invitation>();
  #queue: Promise<unknown> = Promise.resolve();
  #closed = false;
  #poisoned = false;

  private constructor(db: Database, domain: Domain, seed: string, name: string) {
    this.#db = db;
    this.#domain = domain;
    this.seedHex = seed;
    this.name = name;
    this.id = string(object(parse(domain.snapshot())).device_id);
  }

  static async open(databaseName: string, name: string): Promise<BrowserStore> {
    deviceName(name);
    requireThat(/^[a-zA-Z0-9_-]{1,80}\.db$/.test(databaseName), 'Invalid browser database name.');
    const db = await connect(databaseName);
    let domain: Domain | undefined;
    try {
      await db.exec(`PRAGMA synchronous = FULL;
        CREATE TABLE IF NOT EXISTS identity (slot INTEGER PRIMARY KEY CHECK (slot = 1), seed BLOB NOT NULL, name TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS invitations (token_hash TEXT PRIMARY KEY, workspace_ids TEXT NOT NULL, expires_at_ms INTEGER NOT NULL, accepted_peer TEXT) STRICT;`);
      let identity = await (await db.prepare('SELECT seed, name FROM identity WHERE slot = 1')).get();
      if (identity === undefined) {
        // Commit the endpoint/signing seed before any network endpoint exists.
        const seed = crypto.getRandomValues(new Uint8Array(32));
        await (await db.prepare('INSERT INTO identity(slot,seed,name) VALUES(1,?,?)')).run(seed, name);
        identity = { seed, name };
      }
      const identityRow = object(identity);
      requireThat(identityRow.seed instanceof Uint8Array && identityRow.seed.length === 32, 'Stored device key is invalid; refusing to rotate identity.');
      const seedHex = Array.from(identityRow.seed, byte => byte.toString(16).padStart(2, '0')).join('');
      const retainedName = deviceName(identityRow.name);
      const operations: unknown[] = [];
      for (const raw of await (await db.prepare('SELECT id,workspace_id,payload FROM operations ORDER BY id')).all()) {
        const row = object(raw);
        requireThat(operations.length < 4096, 'Stored history exceeds its supported limit.');
        const signed = object(parse(string(row.payload))), op = object(signed.operation);
        requireThat(op.id === row.id && op.workspace_id === row.workspace_id, 'Stored operation index disagrees with its signed payload.');
        operations.push(signed);
      }
      const requests: unknown[] = [];
      for (const raw of await (await db.prepare('SELECT id,payload FROM requests ORDER BY id')).all()) {
        const row = object(raw), command = object(parse(string(row.payload)));
        requireThat(row.id === command.operation_id, 'Stored request index disagrees with its command.');
        requests.push(command);
      }
      domain = new Domain(seedHex, retainedName, json(operations), json(requests));
      const store = new BrowserStore(db, domain, seedHex, retainedName);
      for (const raw of await (await db.prepare('SELECT id,payload FROM peers ORDER BY id')).all()) {
        const row = object(raw);
        const peer = store.#decodePeer(domain.validate_peer(string(row.payload)));
        requireThat(row.id === peer.id, 'Stored contact index disagrees with its payload.');
        store.#peers.set(peer.id, peer);
      }
      for (const raw of await (await db.prepare('SELECT token_hash,workspace_ids,expires_at_ms,accepted_peer FROM invitations')).all()) {
        const row = object(raw), selected = scope(parse(string(row.workspace_ids)));
        const invitation = { token_hash: hex(row.token_hash), workspace_ids: selected, expires_at_ms: number(row.expires_at_ms), accepted_peer: row.accepted_peer === null ? null : hex(row.accepted_peer) };
        requireThat(invitation.expires_at_ms > 0, 'Invalid retained invitation expiry.');
        domain.operations(json(selected));
        store.#invitations.set(invitation.token_hash, invitation);
      }
      return store;
    } catch (error) {
      domain?.free();
      await db.close();
      throw error;
    }
  }

  #run<T>(action: () => T | Promise<T>): Promise<T> {
    if (this.#closed) return Promise.reject(new Error('Browser storage is closed.'));
    const result = this.#queue.then(() => {
      requireThat(!this.#poisoned, 'Storage outcome is uncertain. Close and reopen this tab to recover.');
      return action();
    });
    this.#queue = result.catch(() => undefined);
    return result;
  }

  #decodePeer(value: string): PeerRecord {
    const peer = fields(parse(value), ['id', 'name', 'address_json']);
    return { id: hex(peer.id), name: deviceName(peer.name), address_json: string(peer.address_json) };
  }

  contact(value: unknown): Promise<PeerRecord> {
    return this.#run(() => this.#decodePeer(this.#domain.validate_contact(json(value))));
  }

  async #writePeer(peer: PeerRecord): Promise<void> {
    await (await this.#db.prepare('INSERT INTO peers(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload')).run(peer.id, json(peer));
  }

  async #apply(prepared: Batch, extra?: () => Promise<void>): Promise<void> {
    if (prepared.batch_id === null && !extra) return;
    try {
      await this.#db.exec('BEGIN');
      for (const signed of prepared.operations) {
        await (await this.#db.prepare('INSERT INTO operations(id,workspace_id,payload) VALUES(?,?,?)')).run(signed.operation.id, signed.operation.workspace_id, json(signed));
      }
      if (prepared.request) {
        await (await this.#db.prepare('INSERT INTO requests(id,payload) VALUES(?,?)')).run(prepared.request.operation_id, json(prepared.request));
      }
      if (extra) await extra();
      await this.#db.exec('COMMIT');
      if (prepared.batch_id !== null) this.#domain.commit(prepared.batch_id);
    } catch (error) {
      this.#poisoned = true;
      try { await this.#db.exec('ROLLBACK'); } catch { /* COMMIT may already be durable. */ }
      if (prepared.batch_id !== null) {
        try { this.#domain.discard(prepared.batch_id); } catch { /* Reload is the authority after uncertain commit. */ }
      }
      throw new Error('Storage outcome is uncertain. Close and reopen this tab to recover committed work.', { cause: error });
    }
  }

  snapshot(): Promise<DataSnapshot> {
    return this.#run(() => {
      const value = decodeSnapshot({ ...object(parse(this.#domain.snapshot())), network: { type: 'paused' }, peers: [] });
      const { network: _, peers: __, ...data } = value;
      return data;
    });
  }

  command(command: Command): Promise<Receipt> {
    return this.#run(async () => {
      const prepared = batch(this.#domain.prepare_command(json(command)));
      requireThat(prepared.receipt && 'type' in prepared.receipt, 'Prepared command has no command receipt.');
      await this.#apply(prepared);
      return prepared.receipt;
    });
  }

  allowed(peerId: string): Promise<readonly string[]> {
    return this.#run(() => {
      const result = parse(this.#domain.allowed_workspaces(hex(peerId)));
      requireThat(Array.isArray(result), 'Invalid membership projection.');
      return result.map(string);
    });
  }

  exportFor(peerId: string, selected: readonly string[]): Promise<readonly SignedOperation[]> {
    return this.#run(() => signedOperations(parse(this.#domain.export_for(hex(peerId), json(scope(selected)))), selected));
  }

  fingerprint(selected: readonly string[]): Promise<string> {
    return this.#run(() => hex(this.#domain.fingerprint(json(scope(selected)))));
  }

  import(peerId: string, incoming: readonly SignedOperation[]): Promise<ImportReceipt> {
    return this.#run(async () => {
      const prepared = batch(this.#domain.prepare_import(hex(peerId), json(incoming)));
      await this.#apply(prepared);
      return prepared.receipt && 'imported' in prepared.receipt ? prepared.receipt : ZERO_IMPORT;
    });
  }

  bootstrap(peer: PeerRecord, selected: readonly string[], incoming: readonly SignedOperation[]): Promise<ImportReceipt> {
    return this.#run(async () => {
      const prepared = batch(this.#domain.prepare_bootstrap(peer.id, json(scope(selected)), json(incoming)));
      await this.#apply(prepared, () => this.#writePeer(peer));
      this.#peers.set(peer.id, peer);
      return prepared.receipt && 'imported' in prepared.receipt ? prepared.receipt : ZERO_IMPORT;
    });
  }

  createInvite(tokenHash: string, selected: readonly string[], expiry: number): Promise<void> {
    return this.#run(async () => {
      hex(tokenHash); scope(selected); number(expiry);
      requireThat(expiry > 0, 'Invalid invitation expiry.');
      this.#domain.export_for(this.id, json(selected));
      const invitation: Invitation = { token_hash: tokenHash, workspace_ids: [...selected], expires_at_ms: expiry, accepted_peer: null };
      await this.#apply({ batch_id: null, operations: [] }, async () => {
        await (await this.#db.prepare('INSERT INTO invitations(token_hash,workspace_ids,expires_at_ms,accepted_peer) VALUES(?,?,?,NULL)')).run(tokenHash, json(selected), expiry);
      });
      this.#invitations.set(tokenHash, invitation);
    });
  }

  acceptInvite(tokenHash: string, peer: PeerRecord, now: number): Promise<readonly string[]> {
    return this.#run(async () => {
      hex(tokenHash); number(now);
      requireThat(peer.id !== this.id, 'A host cannot enroll itself.');
      const invitation = this.#invitations.get(tokenHash);
      requireThat(invitation, 'Invitation is unknown.');
      if (invitation.accepted_peer !== null) {
        requireThat(invitation.accepted_peer === peer.id, 'Invitation was already used by another device.');
        // Same authenticated identity can recover a lost acceptance reply after expiry.
        await this.#apply({ batch_id: null, operations: [] }, () => this.#writePeer(peer));
        this.#peers.set(peer.id, peer);
        return invitation.workspace_ids;
      }
      requireThat(now < invitation.expires_at_ms, 'Invitation has expired.');
      const prepared = batch(this.#domain.prepare_grant(tokenHash, json(invitation.workspace_ids), peer.id));
      await this.#apply(prepared, async () => {
        await this.#writePeer(peer);
        await (await this.#db.prepare('UPDATE invitations SET accepted_peer = ? WHERE token_hash = ?')).run(peer.id, tokenHash);
      });
      this.#peers.set(peer.id, peer);
      this.#invitations.set(tokenHash, { ...invitation, accepted_peer: peer.id });
      return invitation.workspace_ids;
    });
  }

  peers(): Promise<readonly PeerRecord[]> { return this.#run(() => [...this.#peers.values()]); }

  savePeer(peer: PeerRecord): Promise<void> {
    return this.#run(async () => {
      const valid = this.#decodePeer(this.#domain.validate_peer(json(peer)));
      if (json(this.#peers.get(valid.id)) === json(valid)) return;
      await this.#apply({ batch_id: null, operations: [] }, () => this.#writePeer(valid));
      this.#peers.set(valid.id, valid);
    });
  }

  sharedContacts(selected: readonly string[]): Promise<readonly Contact[]> {
    return this.#run(() => {
      scope(selected);
      const contacts: Contact[] = [];
      for (const peer of this.#peers.values()) {
        const allowed = parse(this.#domain.allowed_workspaces(peer.id));
        requireThat(Array.isArray(allowed), 'Invalid membership projection.');
        if (peer.id !== this.id && allowed.some(id => selected.includes(id))) {
          const address = object(parse(peer.address_json));
          requireThat(Array.isArray(address.addrs), 'Invalid stored endpoint addresses.');
          contacts.push({ name: peer.name, address: { id: hex(address.id), addrs: address.addrs } });
        }
      }
      requireThat(contacts.length <= 64, 'Too many contacts for one transfer.');
      return contacts;
    });
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    await this.#queue;
    this.#domain.free();
    await this.#db.close();
  }
}
