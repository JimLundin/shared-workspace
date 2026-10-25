/** Device-local encryption effects. Turso never receives a plaintext credential. */
import { fields, json, requireThat } from './types.ts';
import { connectionError, signedConnection, workspaceId, type SignedConnection } from './connections.ts';
import { hex } from './types.ts';

export type SealedConnection = Readonly<{ id: string; workspace_id: string; nonce: string; ciphertext: string }>;
const encode = (value: Uint8Array): string => Array.from(value, byte => byte.toString(16).padStart(2, '0')).join('');
function decode(value: unknown, min: number, max: number): Uint8Array<ArrayBuffer> {
  requireThat(typeof value === 'string' && value.length >= min * 2 && value.length <= max * 2 && /^(?:[0-9a-f]{2})+$/.test(value), 'Invalid encrypted connection.');
  return Uint8Array.from(value.match(/../g)!, item => Number.parseInt(item, 16));
}
function aad(workspace: string, id: string): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(json(['shared-workspace/connection-at-rest/1', workspace, id]));
}
function identityAad(database: string): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(json(['shared-workspace/device-identity-at-rest/1', database, 1]));
}
function validKey(key: unknown): key is CryptoKey {
  return key instanceof CryptoKey && key.type === 'secret' && !key.extractable && key.algorithm.name === 'AES-GCM'
    && 'length' in key.algorithm && key.algorithm.length === 256 && key.usages.length === 2 && key.usages.includes('encrypt') && key.usages.includes('decrypt');
}
function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => { value.onsuccess = () => resolve(value.result); value.onerror = () => reject(connectionError('The device connection key could not be opened.')); });
}
function completed(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = transaction.onerror = () => reject(connectionError('The device connection key could not be saved.'));
  });
}
export class ConnectionVault {
  readonly #key: CryptoKey;
  readonly created: boolean;
  constructor(key: CryptoKey, created = false) {
    requireThat(validKey(key), 'Invalid device encryption key.');
    this.#key = key;
    this.created = created;
  }
  static async open(database: string, hasCiphertext: boolean): Promise<ConnectionVault> {
    const opening = indexedDB.open('shared-workspace-device-keys', 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore('connection-keys');
    const db = await request(opening);
    try {
      const read = db.transaction('connection-keys', 'readonly');
      const [retained]: [unknown, void] = await Promise.all([request(read.objectStore('connection-keys').get(database)), completed(read)]);
      if (retained !== undefined) {
        if (!validKey(retained)) throw connectionError('The retained device connection key is invalid.');
        return new ConnectionVault(retained);
      }
      if (hasCiphertext) throw connectionError('The device connection key is missing. Retained connections cannot be decrypted.');
      const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
      const write = db.transaction('connection-keys', 'readwrite');
      const saved = completed(write);
      write.objectStore('connection-keys').add(key, database);
      await saved;
      return new ConnectionVault(key, true);
    } catch (error) {
      throw connectionError(error instanceof Error && error.name === 'ConnectionError' ? error.message : 'The device connection key could not be opened.');
    } finally { db.close(); }
  }
  async sealIdentity(database: string, seed: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
    try {
      requireThat(seed.length === 32, 'Invalid device seed.');
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: identityAad(database) }, this.#key, seed);
      const result = new Uint8Array(64);
      result.set([0x57, 0x4b, 0x49, 1]); // WKI, version 1; legacy seeds have length 32.
      result.set(nonce, 4);
      result.set(new Uint8Array(ciphertext), 16);
      return result;
    } catch { throw connectionError('The device identity could not be encrypted.'); }
  }
  async unsealIdentity(database: string, value: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
    try {
      requireThat(value.length === 64 && value[0] === 0x57 && value[1] === 0x4b && value[2] === 0x49 && value[3] === 1, 'Invalid encrypted device identity.');
      const plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: value.slice(4, 16), additionalData: identityAad(database) }, this.#key, value.slice(16));
      requireThat(plaintext.byteLength === 32, 'Invalid device seed.');
      return new Uint8Array(plaintext);
    } catch { throw connectionError('The retained device identity could not be decrypted. Refusing to replace it.'); }
  }
  async seal(signed: SignedConnection): Promise<SealedConnection> {
    const { id, workspace_id } = signed.record;
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = new TextEncoder().encode(json(signed));
    try {
      const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad(workspace_id, id) }, this.#key, plaintext);
      return { id, workspace_id, nonce: encode(nonce), ciphertext: encode(new Uint8Array(ciphertext)) };
    } catch { throw connectionError('The workspace connection could not be encrypted.'); }
    finally { plaintext.fill(0); }
  }
  async unseal(value: unknown): Promise<SignedConnection> {
    let plaintext: Uint8Array | undefined;
    try {
      const sealed = fields(value, ['id', 'workspace_id', 'nonce', 'ciphertext']);
      const id = hex(sealed.id), workspace = workspaceId(sealed.workspace_id);
      const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: decode(sealed.nonce, 12, 12), additionalData: aad(workspace, id) }, this.#key, decode(sealed.ciphertext, 16, 32_768));
      plaintext = new Uint8Array(decrypted);
      const signed = signedConnection(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(plaintext)));
      requireThat(signed.record.id === id && signed.record.workspace_id === workspace, 'Encrypted connection index is inconsistent.');
      return signed;
    } catch { throw connectionError('A retained workspace connection could not be decrypted.'); }
    finally { plaintext?.fill(0); }
  }
}
