import init, { BrowserEndpoint } from '../../host-wasm/pkg/browser_workspace_host.js';
import { object, string, number, type JoinStage } from '../../../native/ui/src/protocol.ts';
import { BrowserStore } from './store.ts';
import { encodeTicket, decodeTicket } from './invitation.ts';
import {
  MAX_MESSAGE_BYTES, MAX_SCOPE, bytes, fields, hashNonce, hex, json, parse,
  randomHex, requireThat, safeError, sameScope, scope, signedOperations,
  type Command, type Contact, type Envelope, type HostOptions, type Invite, type Peer,
  type PeerRecord, type PeerState, type Receipt, type ReplyBody, type RequestBody,
  type Snapshot, type SyncReport, type Ticket,
} from './types.ts';

function contactFromRecord(peer: PeerRecord): Contact {
  const address = object(parse(peer.address_json));
  requireThat(Array.isArray(address.addrs), 'Invalid endpoint address.');
  return { name: peer.name, address: { id: hex(address.id), addrs: address.addrs } };
}

async function retireEndpoint(endpoint: BrowserEndpoint): Promise<void> {
  await endpoint.close();
  endpoint.free();
}

/** Network effects are outside BrowserStore's queue. */
export class HostOwner {
  readonly #store: BrowserStore;
  readonly #baseUrl?: string;
  #endpoint: BrowserEndpoint | null = null;
  #networkError: string | null = null;
  #foreground: boolean;
  #generation = 0;
  #closed = false;
  #lifecycle: Promise<unknown> = Promise.resolve();
  #syncing: Promise<SyncReport> | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  readonly #observations = new Map<string, PeerState>();

  private constructor(store: BrowserStore, options: HostOptions) {
    this.#store = store;
    this.#baseUrl = options.baseUrl;
    this.#foreground = !options.startPaused;
  }

  static async open(options: HostOptions): Promise<HostOwner> {
    await init();
    const store = await BrowserStore.open(options.databaseName ?? 'shared-workspaces.db', options.deviceName ?? 'Browser host');
    const owner = new HostOwner(store, options);
    await owner.#maintain();
    owner.#timer = setInterval(() => owner.#wake(), 5000);
    return owner;
  }

  #serialLifecycle(action: () => Promise<void>): Promise<void> {
    const result = this.#lifecycle.then(action);
    this.#lifecycle = result.catch(() => undefined);
    return result;
  }

  #maintain(): Promise<void> {
    return this.#serialLifecycle(async () => {
      if (this.#closed || !this.#foreground || this.#endpoint) return;
      const generation = this.#generation;
      let started: BrowserEndpoint | undefined;
      try {
        const endpoint = await BrowserEndpoint.start(this.#store.seedHex, (peer: string, payload: string) => this.#receive(peer, payload));
        started = endpoint;
        requireThat(endpoint.id() === this.#store.id, 'Transport and signing identities differ.');
        if (generation !== this.#generation || this.#closed || !this.#foreground) {
          started = undefined;
          await retireEndpoint(endpoint);
          return;
        }
        this.#endpoint = endpoint;
        started = undefined;
        this.#networkError = null;
      } catch (error) {
        if (started) await retireEndpoint(started);
        if (generation === this.#generation && !this.#closed && this.#foreground) this.#networkError = safeError(error);
      }
    });
  }

  #wake(): void {
    if (this.#closed || !this.#foreground) return;
    void this.#maintain().then(() => {
      if (!this.#closed && this.#foreground && this.#endpoint && !this.#syncing) return this.sync();
    }).catch(() => undefined);
  }

  #client(): BrowserEndpoint {
    requireThat(!this.#closed, 'Browser host is closed.');
    requireThat(this.#foreground, 'Networking is paused.');
    requireThat(this.#endpoint, this.#networkError ?? 'Networking is unavailable.');
    return this.#endpoint;
  }

  async #ownContact(endpoint: BrowserEndpoint): Promise<Contact> {
    const address = parse(await endpoint.address());
    return contactFromRecord(await this.#store.contact({ name: this.#store.name, address }));
  }

  async #contacts(value: unknown): Promise<readonly Contact[]> {
    requireThat(Array.isArray(value) && value.length <= 64, 'Too many peer contacts.');
    return Promise.all(value.map(async contact => contactFromRecord(await this.#store.contact(contact))));
  }

  async #retain(selected: readonly string[], contacts: readonly Contact[]): Promise<void> {
    const records = await Promise.all(contacts.map(contact => this.#store.contact(contact)));
    for (const record of records) {
      const allowed = await this.#store.allowed(record.id);
      requireThat(allowed.some(id => selected.includes(id)), 'Contact is not a member of a selected workspace.');
    }
    for (const record of records) if (record.id !== this.#store.id) await this.#store.savePeer(record);
  }

  async #authorize(peer: string, selected: readonly string[]): Promise<void> {
    const allowed = await this.#store.allowed(peer);
    requireThat(selected.every(id => allowed.includes(id)), 'Peer is not authorized for every selected workspace.');
  }

  async #envelope(payload: string, authenticatedPeer: string): Promise<Envelope<unknown>> {
    requireThat(bytes(payload) <= MAX_MESSAGE_BYTES, 'Peer message exceeds 8 MiB.');
    const packet = fields(parse(payload), ['version', 'id', 'contact', 'body']);
    requireThat(packet.version === 1, 'Unsupported peer protocol.');
    const record = await this.#store.contact(packet.contact);
    requireThat(record.id === hex(authenticatedPeer), 'Claimed contact differs from authenticated peer.');
    return { version: 1, id: hex(packet.id), contact: contactFromRecord(record), body: packet.body };
  }

  async #replyBody(value: unknown): Promise<ReplyBody> {
    const result = object(value);
    switch (result.type) {
      case 'need_sync': case 'rejected':
        fields(result, ['type']);
        return { type: result.type };
      case 'current': {
        fields(result, ['type', 'workspace_ids', 'contacts']);
        return { type: result.type, workspace_ids: scope(result.workspace_ids), contacts: await this.#contacts(result.contacts) };
      }
      case 'paired': case 'synced': {
        fields(result, ['type', 'workspace_ids', 'operations', 'contacts']);
        const selected = scope(result.workspace_ids);
        return { type: result.type, workspace_ids: selected, operations: signedOperations(result.operations, selected), contacts: await this.#contacts(result.contacts) };
      }
      default: throw new Error('Unknown peer reply.');
    }
  }

  async #exchange(peer: PeerRecord, body: RequestBody, progress?: (stage: JoinStage) => void): Promise<Envelope<ReplyBody>> {
    const endpoint = this.#client();
    const id = randomHex();
    progress?.('connecting_relay');
    const packet: Envelope<RequestBody> = { version: 1, id, contact: await this.#ownContact(endpoint), body };
    const outgoing = json(packet);
    requireThat(bytes(outgoing) <= MAX_MESSAGE_BYTES, 'Workspace transfer exceeds 8 MiB.');
    requireThat(this.#client() === endpoint, 'Networking changed before this exchange started.');
    progress?.('contacting_device');
    const response = fields(parse(await endpoint.request(peer.address_json, outgoing)), ['peer_id', 'payload']);
    requireThat(response.peer_id === peer.id, 'Transport reply changed peer identity.');
    const reply = await this.#envelope(string(response.payload), peer.id);
    requireThat(reply.id === id, 'Peer reply does not match the request.');
    return { ...reply, body: await this.#replyBody(reply.body) };
  }

  async #receive(authenticatedPeer: string, payload: string): Promise<string> {
    const endpoint = this.#client();
    const request = await this.#envelope(payload, authenticatedPeer);
    let body: ReplyBody;
    try { body = await this.#handle(authenticatedPeer, request); }
    catch { body = { type: 'rejected' }; }
    const reply = json({ version: 1, id: request.id, contact: await this.#ownContact(endpoint), body });
    requireThat(bytes(reply) <= MAX_MESSAGE_BYTES, 'Workspace response exceeds 8 MiB.');
    return reply;
  }

  async #handle(peerId: string, request: Envelope<unknown>): Promise<ReplyBody> {
    const body = object(request.body);
    const peer = await this.#store.contact(request.contact);
    switch (body.type) {
      case 'pair': {
        fields(body, ['type', 'nonce']);
        const selected = await this.#store.acceptInvite(await hashNonce(hex(body.nonce)), peer, Date.now());
        return { type: 'paired', workspace_ids: selected, operations: await this.#store.exportFor(peerId, selected), contacts: await this.#store.sharedContacts(selected) };
      }
      case 'summary': {
        fields(body, ['type', 'workspace_ids', 'fingerprint', 'contacts']);
        const selected = scope(body.workspace_ids), fingerprint = hex(body.fingerprint);
        const contacts = await this.#contacts(body.contacts);
        await this.#authorize(peerId, selected);
        if (await this.#store.fingerprint(selected) !== fingerprint) return { type: 'need_sync' };
        await this.#store.savePeer(peer);
        await this.#retain(selected, contacts);
        return { type: 'current', workspace_ids: selected, contacts: await this.#store.sharedContacts(selected) };
      }
      case 'sync': {
        fields(body, ['type', 'workspace_ids', 'operations', 'contacts']);
        const selected = scope(body.workspace_ids), operations = signedOperations(body.operations, selected);
        const contacts = await this.#contacts(body.contacts);
        // A newly granted member may carry its grant while the inviter is offline.
        await this.#store.import(peerId, operations);
        await this.#authorize(peerId, selected);
        await this.#store.savePeer(peer);
        await this.#retain(selected, contacts);
        return { type: 'synced', workspace_ids: selected, operations: await this.#store.exportFor(peerId, selected), contacts: await this.#store.sharedContacts(selected) };
      }
      default: throw new Error('Unknown peer request.');
    }
  }

  async #peerStatuses(): Promise<readonly Peer[]> {
    return Promise.all((await this.#store.peers()).map(async peer => ({ id: peer.id, name: peer.name, workspace_ids: await this.#store.allowed(peer.id), state: this.#observations.get(peer.id) ?? { type: 'never_synced' } })));
  }

  async snapshot(): Promise<Snapshot> {
    const network: Snapshot['network'] = this.#closed ? { type: 'closed' } : !this.#foreground ? { type: 'paused' } : this.#endpoint ? { type: 'active' } : { type: 'unavailable', detail: this.#networkError ?? 'Connecting to the peer network.' };
    return { ...await this.#store.snapshot(), network, peers: await this.#peerStatuses() };
  }

  async command(command: Command): Promise<Receipt> {
    requireThat(!this.#closed, 'Browser host is closed.');
    const receipt = await this.#store.command(command);
    this.#wake();
    return receipt;
  }

  async invite(workspaceIds: readonly string[]): Promise<Invite> {
    const endpoint = this.#client(), selected = scope(workspaceIds);
    const nonce = randomHex(), expiry = Date.now() + 900_000;
    const ticket: Ticket = { version: 1, inviter: await this.#ownContact(endpoint), nonce, workspace_ids: selected, expires_at_ms: expiry };
    const text = encodeTicket(ticket, this.#baseUrl);
    await this.#store.createInvite(await hashNonce(nonce), selected, expiry);
    return { ticket: text, expires_at_ms: expiry };
  }

  async join(value: string, progress?: (stage: JoinStage) => void): Promise<Snapshot> {
    progress?.('checking_invitation');
    this.#client();
    const decoded = decodeTicket(value);
    requireThat(decoded.version === 1, 'Unsupported invitation version.');
    const inviter = await this.#store.contact(decoded.inviter), selected = scope(decoded.workspace_ids);
    const nonce = hex(decoded.nonce);
    number(decoded.expires_at_ms);
    requireThat(inviter.id !== this.#store.id, 'A host cannot join itself.');
    const reply = await this.#exchange(inviter, { type: 'pair', nonce }, progress);
    requireThat(reply.body.type === 'paired', 'Invitation could not be accepted; it may be expired or already used.');
    requireThat(sameScope(reply.body.workspace_ids, selected), 'Pairing reply changed the selected workspaces.');
    progress?.('saving_workspace');
    await this.#store.bootstrap(await this.#store.contact(reply.contact), selected, reply.body.operations);
    await this.#retain(selected, reply.body.contacts);
    this.#wake();
    return this.snapshot();
  }

  async #syncPeer(peer: PeerRecord): Promise<number> {
    const all = await this.#store.allowed(peer.id);
    requireThat(all.length > 0, 'No shared workspaces.');
    let imported = 0;
    for (let start = 0; start < all.length; start += MAX_SCOPE) {
      this.#client();
      const selected = all.slice(start, start + MAX_SCOPE);
      const contacts = await this.#store.sharedContacts(selected);
      const summary = await this.#exchange(peer, { type: 'summary', workspace_ids: selected, fingerprint: await this.#store.fingerprint(selected), contacts });
      if (summary.body.type === 'current') {
        requireThat(sameScope(summary.body.workspace_ids, selected), 'Summary reply changed the selected workspaces.');
        await this.#store.savePeer(await this.#store.contact(summary.contact));
        await this.#retain(selected, summary.body.contacts);
        continue;
      }
      requireThat(summary.body.type === 'need_sync' || summary.body.type === 'rejected', 'Invalid synchronization summary.');
      const reply = await this.#exchange(peer, { type: 'sync', workspace_ids: selected, operations: await this.#store.exportFor(peer.id, selected), contacts });
      requireThat(reply.body.type === 'synced', 'Peer could not complete synchronization.');
      requireThat(sameScope(reply.body.workspace_ids, selected), 'Sync reply changed the selected workspaces.');
      imported += (await this.#store.import(peer.id, reply.body.operations)).imported;
      await this.#store.savePeer(await this.#store.contact(reply.contact));
      await this.#retain(selected, reply.body.contacts);
    }
    return imported;
  }

  sync(): Promise<SyncReport> {
    // An explicit sync must include edits made since an earlier background pass
    // captured its summary. Await that pass, then perform a fresh exchange.
    if (this.#syncing) return this.#syncing.catch(() => undefined).then(() => this.sync());
    const generation = this.#generation;
    const task = (async () => {
      this.#client();
      for (const peer of await this.#store.peers()) {
        if (generation !== this.#generation || !this.#foreground || this.#closed) break;
        this.#observations.set(peer.id, { type: 'syncing' });
        let state: PeerState;
        try { state = { type: 'synced', at_ms: Date.now(), imported: await this.#syncPeer(peer) }; }
        catch (error) { state = { type: 'unreachable', at_ms: Date.now(), detail: safeError(error) }; }
        if (generation === this.#generation && !this.#closed && this.#foreground) this.#observations.set(peer.id, state);
      }
      return { peers: await this.#peerStatuses() };
    })();
    this.#syncing = task;
    void task.finally(() => { if (this.#syncing === task) this.#syncing = null; }).catch(() => undefined);
    return task;
  }

  setForeground(foreground: boolean): Promise<void> {
    requireThat(!this.#closed, 'Browser host is closed.');
    this.#foreground = foreground;
    this.#generation++;
    if (!foreground) {
      for (const [id, state] of this.#observations) {
        if (state.type === 'syncing') this.#observations.set(id, { type: 'unreachable', at_ms: Date.now(), detail: 'Networking paused before this exchange completed.' });
      }
    }
    return this.#serialLifecycle(async () => {
      if (!this.#foreground && this.#endpoint) {
        const endpoint = this.#endpoint;
        this.#endpoint = null;
        await retireEndpoint(endpoint);
      }
    }).then(async () => {
      if (this.#foreground && !this.#closed) {
        await this.#maintain();
        this.#wake();
      }
    });
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    this.#foreground = false;
    this.#generation++;
    clearInterval(this.#timer);
    try {
      await this.#serialLifecycle(async () => {
        const endpoint = this.#endpoint;
        this.#endpoint = null;
        if (endpoint) await retireEndpoint(endpoint);
      });
    } finally {
      try { await this.#syncing?.catch(() => undefined); }
      finally { await this.#store.close(); }
    }
  }
}
