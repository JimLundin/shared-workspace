import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ConnectionVault } from '../src/connection-vault.ts';
const key = () => crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
const record = { record: { version: 1, id: 'aa'.repeat(32), workspace_id: 'work', author: 'bb'.repeat(32), parents: [], action: { type: 'set', api_key: 'SYNTHETIC_ONLY_VAULT_CREDENTIAL', model: 'gpt-4o-transcribe' } }, signature: 'ef'.repeat(64) };
test('sealed records contain ciphertext and decrypt with the retained nonextractable device key', async () => {
  const deviceKey = await key(), vault = new ConnectionVault(deviceKey);
  await assert.rejects(crypto.subtle.exportKey('raw', deviceKey));
  const first = await vault.seal(record), second = await vault.seal(record);
  assert.ok(!JSON.stringify(first).includes(record.record.action.api_key), 'PLAINTEXT_RETAINED_IN_DATABASE');
  assert.notEqual(first.nonce, second.nonce);
  assert.notEqual(first.ciphertext, second.ciphertext);
  assert.deepEqual(await new ConnectionVault(deviceKey).unseal(first), record);
});
test('ciphertext cannot be transplanted to another workspace or revision', async () => {
  const vault = new ConnectionVault(await key()), sealed = await vault.seal(record);
  await assert.rejects(vault.unseal({ ...sealed, workspace_id: 'private' }), undefined, 'CIPHERTEXT_WORKSPACE_REBOUND');
  await assert.rejects(vault.unseal({ ...sealed, id: 'cc'.repeat(32) }), undefined, 'CIPHERTEXT_REVISION_REBOUND');
});
test('another device must receive the signed record and reencrypt with its own key', async () => {
  const a = new ConnectionVault(await key()), b = new ConnectionVault(await key());
  const sealed = await a.seal(record);
  await assert.rejects(b.unseal(sealed), undefined, 'DEVICE_KEY_IGNORED');
  const resealed = await b.seal(await a.unseal(sealed));
  assert.notEqual(sealed.ciphertext, resealed.ciphertext);
  assert.deepEqual(await b.unseal(resealed), record);
});
test('tampered encryption data fails with a fixed error and no reflected plaintext', async () => {
  const vault = new ConnectionVault(await key()), sealed = await vault.seal(record);
  const tampered = { ...sealed, ciphertext: (sealed.ciphertext.startsWith('00') ? '01' : '00') + sealed.ciphertext.slice(2) };
  await assert.rejects(vault.unseal(tampered), { message: 'A retained workspace connection could not be decrypted.' });
  await assert.rejects(vault.unseal({ ...sealed, ciphertext: record.record.action.api_key }), { message: 'A retained workspace connection could not be decrypted.' });
});
test('exportable or inappropriate encryption keys are refused', async () => {
  const exportable = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
  assert.throws(() => new ConnectionVault(exportable), undefined, 'EXTRACTABLE_DEVICE_KEY_ACCEPTED');
});

test('encrypted device identity preserves every seed byte and binds database and slot', async () => {
  const vault = new ConnectionVault(await key());
  const seed = Uint8Array.from({ length: 32 }, (_, index) => index + 7);
  const encrypted = await vault.sealIdentity('work.db', seed);
  assert.equal(encrypted.length, 64);
  assert.deepEqual([...encrypted.slice(0, 4)], [0x57, 0x4b, 0x49, 1]);
  assert.ok(!Buffer.from(encrypted).includes(Buffer.from(seed)), 'PLAINTEXT_IDENTITY_RETAINED');
  assert.deepEqual(await vault.unsealIdentity('work.db', encrypted), seed);
  await assert.rejects(vault.unsealIdentity('other.db', encrypted), undefined, 'IDENTITY_DATABASE_BINDING_REMOVED');
  await assert.rejects(new ConnectionVault(await key()).unsealIdentity('work.db', encrypted));
});

test('tampered encrypted identity cannot silently become a new seed', async () => {
  const vault = new ConnectionVault(await key());
  const encrypted = await vault.sealIdentity('work.db', new Uint8Array(32).fill(7));
  for (const offset of [0, 3, 4, 32, 63]) {
    const changed = encrypted.slice(); changed[offset] ^= 1;
    await assert.rejects(vault.unsealIdentity('work.db', changed), { message: 'The retained device identity could not be decrypted. Refusing to replace it.' });
  }
  await assert.rejects(vault.unsealIdentity('work.db', encrypted.slice(0, 32)));
});
