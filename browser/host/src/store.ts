import { connect } from '@tursodatabase/database-wasm';
import { Domain } from '../../host-wasm/pkg/browser_workspace_host.js';
import { decodeSnapshot, object, string, number } from '../../../native/ui/src/protocol.ts';
import { ConnectionVault, type SealedConnection } from './connection-vault.ts';
import { verifyIdentityStorage } from './identity-storage.ts';
import {
  MAX_CONNECTION_RECORDS, activeConnection, connectionError, connectionFailure, connectionPayload, connectionRecords,
  connectionState, mergeConnections, prepareConnection, publicConnection, removeConnection, setConnection, workspaceId,
  type ConnectionReceipt, type ConnectionState, type PublicConnection, type RemoveConnection, type SetConnection, type SignedConnection,
} from './connections.ts';
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
  readonly #vault: ConnectionVault;
  #connections: readonly PublicConnection[] = [];
  readonly #sealedConnections = new Map<string, SealedConnection>();
  readonly #peers = new Map<string, PeerRecord>();
  readonly #invitations = new Map<string, Invitation>();
  #queue: Promise<unknown> = Promise.resolve();
  #closed = false;
  #poisoned = false;

  private constructor(db: Database, domain: Domain, seed: string, name: string, vault: ConnectionVault) {
    this.#db = db;
    this.#domain = domain;
    this.#vault = vault;
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
        CREATE TABLE IF NOT EXISTS identity_format (slot INTEGER PRIMARY KEY CHECK (slot = 1), version INTEGER NOT NULL CHECK (version = 1)) STRICT;
        CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
        CREATE TABLE IF NOT EXISTS invitations (token_hash TEXT PRIMARY KEY, workspace_ids TEXT NOT NULL, expires_at_ms INTEGER NOT NULL, accepted_peer TEXT) STRICT;
        CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, nonce TEXT NOT NULL, ciphertext TEXT NOT NULL) STRICT;`);
      let identity = await (await db.prepare('SELECT seed, name FROM identity WHERE slot = 1')).get();
      const format = await (await db.prepare('SELECT version FROM identity_format WHERE slot = 1')).get();
      requireThat(format === undefined || (identity !== undefined && object(format).version === 1), 'Stored device identity format is invalid; refusing to rotate identity.');
      const sealed = await (await db.prepare('SELECT id,workspace_id,nonce,ciphertext FROM connections ORDER BY id')).all();
      requireThat(sealed.length <= MAX_CONNECTION_RECORDS, 'Retained connection history exceeds its supported limit.');
      if (identity !== undefined) {
        const stored = object(identity).seed;
        requireThat(stored instanceof Uint8Array && (stored.length === 32 || stored.length === 64), 'Stored device key is invalid; refusing to rotate identity.');
        requireThat(format === undefined || stored.length === 64, 'The encrypted device identity is damaged; refusing to reinterpret it as a legacy seed.');
      }
      const encryptedIdentity = identity !== undefined && (object(identity).seed as Uint8Array).length === 64;
      const vault = await ConnectionVault.open(databaseName, sealed.length > 0 || encryptedIdentity);
      if (identity === undefined) {
        if (!vault.created) throw connectionError('A retained device key exists but its database identity is missing. Restore the original workspace data; no replacement identity was created.');
        // Commit the endpoint/signing seed before any network endpoint exists.
        const seed = crypto.getRandomValues(new Uint8Array(32));
        try {
          const encrypted = await vault.sealIdentity(databaseName, seed);
          await db.exec('BEGIN');
          await (await db.prepare('INSERT INTO identity(slot,seed,name) VALUES(1,?,?)')).run(encrypted, name);
          await db.exec('INSERT INTO identity_format(slot,version) VALUES(1,1)');
          await db.exec('COMMIT');
          identity = { seed: encrypted, name };
        } catch {
          try { await db.exec('ROLLBACK'); } catch { /* A reopen must inspect the original commit outcome. */ }
          throw connectionError('Device identity initialization is unconfirmed. Close and reopen this tab; no replacement identity will be created.');
        } finally { seed.fill(0); }
      }
      const identityRow = object(identity);
      requireThat(identityRow.seed instanceof Uint8Array, 'Stored device key is invalid; refusing to rotate identity.');
      let seed: Uint8Array<ArrayBuffer>;
      if (identityRow.seed.length === 32) {
        seed = new Uint8Array(identityRow.seed);
        const encrypted = await vault.sealIdentity(databaseName, seed);
        // The separately retained device key is durable before migration starts.
        // On unknown commit outcome, reopening reads the original or ciphertext;
        // either represents the exact same device identity, never a new seed.
        try {
          await db.exec('BEGIN');
          await (await db.prepare('UPDATE identity SET seed = ? WHERE slot = 1')).run(encrypted);
          await db.exec('INSERT INTO identity_format(slot,version) VALUES(1,1)');
          await db.exec('COMMIT');
        } catch {
          seed.fill(0);
          try { await db.exec('ROLLBACK'); } catch { /* Reopening determines whether migration committed. */ }
          throw connectionError('Device identity migration is unconfirmed. Close and reopen this tab to recover the same identity.');
        }
      } else {
        seed = await vault.unsealIdentity(databaseName, new Uint8Array(identityRow.seed));
        // An interim encrypted profile is accepted only after authenticated
        // decryption, then explicitly marked without changing its identity.
        if (format === undefined) {
          try { await db.exec('INSERT INTO identity_format(slot,version) VALUES(1,1) ON CONFLICT(slot) DO NOTHING'); }
          catch { seed.fill(0); throw connectionError('Device identity format could not be retained. Reopen this tab to recover.'); }
        }
      }
      let seedHex: string;
      try {
        // Superseded WAL frames can still contain the old plaintext seed after
        // UPDATE. Checkpoint, then verify the actual current files before any
        // Domain or endpoint can expose shared connections. Reopening repeats
        // this barrier after a crash at any migration/checkpoint boundary.
        await verifyIdentityStorage(databaseName, seed, () => db.exec('PRAGMA wal_checkpoint(TRUNCATE)', { queryTimeout: 15_000 }));
        seedHex = Array.from(seed, byte => byte.toString(16).padStart(2, '0')).join('');
      } finally {
        seed.fill(0);
        if (identityRow.seed.length === 32) identityRow.seed.fill(0);
      }
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
      const store = new BrowserStore(db, domain, seedHex, retainedName, vault);
      try {
        const retained = await Promise.all(sealed.map(row => vault.unseal(row)));
        for (const item of retained) store.#verifyConnection(item);
        store.#connections = mergeConnections([], retained).map(publicConnection);
        for (const raw of sealed) {
          const row = object(raw);
          const item = { id: hex(row.id), workspace_id: string(row.workspace_id), nonce: string(row.nonce), ciphertext: string(row.ciphertext) };
          store.#sealedConnections.set(item.id, item);
        }
      } catch { throw connectionError('Retained workspace connections could not be restored.'); }
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
    // The sequencing promise must not retain a successful result: worker-only
    // connection reads can return transient decrypted material to their caller.
    this.#queue = result.then(() => undefined, () => undefined);
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

  #connectionWork<T>(action: () => T | Promise<T>): Promise<T> {
    return this.#run(action).catch(error => { throw connectionError(connectionFailure(error)); });
  }

  #connectionMembership(peer: string, selected: readonly string[]): void {
    for (const device of [this.id, hex(peer)]) {
      const allowed = parse(this.#domain.allowed_workspaces(device));
      requireThat(Array.isArray(allowed) && selected.every(workspace => allowed.includes(workspace)), 'Connection workspace membership is required.');
    }
  }

  #verifyConnection(item: SignedConnection): void {
    this.#domain.verify_connection(item.record.workspace_id, item.record.author, connectionPayload(item.record), item.signature);
  }

  async #persistConnections(next: readonly SignedConnection[]): Promise<void> {
    const retained = new Set(this.#connections.map(item => item.record.id));
    const added = next.filter(item => !retained.has(item.record.id));
    if (!added.length) return;
    requireThat(this.#connections.length + added.length <= MAX_CONNECTION_RECORDS, 'Connection history limit reached.');
    const encrypted = await Promise.all(added.map(item => this.#vault.seal(item)));
    await this.#apply({ batch_id: null, operations: [] }, async () => {
      for (const row of encrypted) {
        await (await this.#db.prepare('INSERT INTO connections(id,workspace_id,nonce,ciphertext) VALUES(?,?,?,?)')).run(row.id, row.workspace_id, row.nonce, row.ciphertext);
      }
    });
    this.#connections = [...this.#connections, ...added.map(publicConnection)].sort((a, b) => a.record.id.localeCompare(b.record.id));
    for (const row of encrypted) this.#sealedConnections.set(row.id, row);
  }

  #decryptConnections(selected: readonly string[]): Promise<readonly SignedConnection[]> {
    return Promise.all([...this.#sealedConnections.values()].filter(row => selected.includes(row.workspace_id)).map(row => this.#vault.unseal(row)));
  }

  connection(workspace: string): Promise<ConnectionState> {
    return this.#connectionWork(() => {
      workspaceId(workspace);
      this.#connectionMembership(this.id, [workspace]);
      return connectionState(this.#connections, workspace);
    });
  }

  setConnection(value: SetConnection): Promise<ConnectionReceipt> {
    return this.#connectionWork(async () => {
      const input = setConnection(value);
      return this.#changeConnection(input, { type: 'set', api_key: input.api_key, model: input.model });
    });
  }

  removeConnection(value: RemoveConnection): Promise<ConnectionReceipt> {
    return this.#connectionWork(async () => this.#changeConnection(removeConnection(value), { type: 'remove' }));
  }

  async #changeConnection(input: RemoveConnection, action: SignedConnection['record']['action']): Promise<ConnectionReceipt> {
    this.#connectionMembership(this.id, [input.workspace_id]);
    const retained = await this.#decryptConnections([input.workspace_id]);
    // A request identity cannot be borrowed from another workspace.
    requireThat(!this.#sealedConnections.has(input.operation_id) || retained.some(item => item.record.id === input.operation_id), 'Connection request identity belongs to another workspace.');
    const prepared = prepareConnection(retained, this.id, input, action);
    if (!prepared.replayed) {
      const signature = this.#domain.sign_connection(input.workspace_id, connectionPayload(prepared.record));
      const signed = { record: prepared.record, signature };
      await this.#persistConnections(mergeConnections(retained, [signed]));
    }
    return { operation_id: input.operation_id, replayed: prepared.replayed, state: connectionState(this.#connections, input.workspace_id) };
  }

  /** Worker-only resolution; never dispatch this method to the UI. */
  connectionCredential(workspace: string, version: string): Promise<ReturnType<typeof activeConnection>> {
    return this.#connectionWork(async () => {
      workspaceId(workspace); hex(version);
      this.#connectionMembership(this.id, [workspace]);
      const state = connectionState(this.#connections, workspace);
      if (state.status !== 'ready' || state.version !== version) throw connectionError('The workspace connection changed or is unavailable. Record again after reviewing its settings.');
      const sealed = this.#sealedConnections.get(version);
      requireThat(sealed, 'Connection is unavailable.');
      return activeConnection([await this.#vault.unseal(sealed)], workspace, version);
    });
  }

  connectionFingerprint(peer: string, selected: readonly string[]): Promise<string> {
    return this.#connectionWork(async () => {
      scope(selected); this.#connectionMembership(peer, selected);
      const summary = this.#connections.filter(item => selected.includes(item.record.workspace_id)).map(item => [item.record.id, item.signature]);
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(json(summary)));
      return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    });
  }

  exportConnections(peer: string, selected: readonly string[]): Promise<readonly SignedConnection[]> {
    return this.#connectionWork(() => {
      scope(selected); this.#connectionMembership(peer, selected);
      return this.#decryptConnections(selected);
    });
  }

  importConnections(peer: string, selected: readonly string[], incoming: unknown): Promise<boolean> {
    return this.#connectionWork(async () => {
      scope(selected); this.#connectionMembership(peer, selected);
      const parsed = connectionRecords(incoming, selected);
      for (const item of parsed) this.#verifyConnection(item);
      const retained = await this.#decryptConnections(selected);
      requireThat(parsed.every(item => !this.#sealedConnections.has(item.record.id) || retained.some(previous => previous.record.id === item.record.id)), 'Connection identity belongs to another workspace.');
      await this.#persistConnections(mergeConnections(retained, parsed));
      const incomingIds = new Set(parsed.map(item => item.record.id));
      return this.#connections.filter(item => selected.includes(item.record.workspace_id)).every(item => incomingIds.has(item.record.id));
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
