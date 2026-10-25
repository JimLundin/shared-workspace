import assert from 'node:assert/strict';
import { test } from 'node:test';
import { encodeTicket, decodeTicket } from '../src/invitation.ts';

const base = 'https://jimlundin.github.io/shared-workspace/';
const ticket = {
  version: 1,
  inviter: { name: 'Web browser', address: { id: '12'.repeat(32), addrs: [{ Relay: 'https://euc1-1.relay.n0.iroh.link./' }] } },
  nonce: 'a7'.repeat(32), workspace_ids: ['6eef8dde-2b03-4fbd-8ee0-e203d4f01242'], expires_at_ms: 1792941800123,
};
const legacy = value => 'workspace-browser-v1.' + Buffer.from(JSON.stringify(value)).toString('base64url');

test('short invitation retains every authority and routing field', () => {
  const link = encodeTicket(ticket, base);
  assert.ok(link.length < 340, `Invitation is still ${link.length} characters`);
  assert.ok(link.length < (base + '#invite=' + legacy(ticket)).length * 0.65);
  assert.deepEqual(decodeTicket(link), ticket);
  assert.deepEqual(decodeTicket(encodeTicket(ticket)), ticket);
});

test('existing raw tickets and HTTPS invitation links remain usable', () => {
  assert.deepEqual(decodeTicket(legacy(ticket)), ticket);
  assert.deepEqual(decodeTicket(base + '#invite=' + legacy(ticket)), ticket);
});

test('multiple workspaces and Unicode device names preserve exact scope', () => {
  const selected = { ...ticket, inviter: { ...ticket.inviter, name: 'Jims telefon 📱' }, workspace_ids: Array.from({ length: 32 }, (_, i) => 'workspace_' + i) };
  assert.deepEqual(decodeTicket(encodeTicket(selected)), selected);
  assert.throws(() => encodeTicket({ ...selected, workspace_ids: [...selected.workspace_ids, 'one_more'] }));
  assert.throws(() => encodeTicket({ ...ticket, workspace_ids: ['same', 'same'] }));
});

test('truncated records and trailing bytes never produce a partial invitation', () => {
  const encoded = encodeTicket(ticket).slice(3), raw = Buffer.from(encoded, 'base64url');
  for (let length = 0; length < raw.length; length++) {
    assert.throws(() => decodeTicket('w2.' + raw.subarray(0, length).toString('base64url')), `Accepted ${length} bytes`);
  }
  assert.throws(() => decodeTicket('w2.' + Buffer.concat([raw, Buffer.from([0])]).toString('base64url')), undefined, 'TRAILING_BYTES_ACCEPTED');
});

test('invalid records and ambiguous links are rejected at the codec boundary', () => {
  assert.throws(() => encodeTicket({ ...ticket, nonce: 'short' }));
  assert.throws(() => encodeTicket({ ...ticket, expires_at_ms: Number.MAX_SAFE_INTEGER + 1 }));
  assert.throws(() => decodeTicket(base + '#invite=' + legacy(ticket) + '&invite=' + legacy(ticket)));
  assert.throws(() => decodeTicket('w2.' + 'x'.repeat(16000)));
  assert.throws(() => decodeTicket('w2.!!!!'));
  assert.throws(() => decodeTicket(legacy({ ...ticket, version: 99 })));
  assert.throws(() => decodeTicket(legacy({ ...ticket, inviter: { ...ticket.inviter, address: { ...ticket.inviter.address, addrs: 'wrong' } } })));
});

// Build untrusted wire records independently of encodeTicket so decoder guards
// are exercised even when the encoder would refuse to create the input.
function compactRecord({ version = 2, name = Buffer.from('Jim'), selected = ['launch'],
  expiry = BigInt(ticket.expires_at_ms), routes = Buffer.from('[]') } = {}) {
  const expires = Buffer.alloc(8); expires.writeBigUInt64BE(expiry);
  const routeLength = Buffer.alloc(2); routeLength.writeUInt16BE(routes.length);
  return Buffer.concat([
    Buffer.from([version]), Buffer.from(ticket.inviter.address.id, 'hex'), Buffer.from(ticket.nonce, 'hex'), expires,
    Buffer.from([name.length]), name, Buffer.from([selected.length]),
    ...selected.flatMap(value => { const text = Buffer.from(value); return [Buffer.from([text.length]), text]; }),
    routeLength, routes,
  ]);
}
const compactText = raw => 'w2.' + raw.toString('base64url');

test('compact records preserve every peer identity byte, nonce byte, and selected workspace', () => {
  const exact = { ...ticket, inviter: { name: '🙂'.repeat(30), address: { id: Array.from({ length: 32 }, (_, i) => i.toString(16).padStart(2, '0')).join(''), addrs: [] } },
    nonce: Array.from({ length: 32 }, (_, i) => (255 - i).toString(16)).join(''),
    workspace_ids: ['a', 'Z'.repeat(128), 'second_workspace'], expires_at_ms: Number.MAX_SAFE_INTEGER };
  const observed = decodeTicket(encodeTicket(exact));
  assert.equal(observed.inviter.address.id, exact.inviter.address.id, 'PEER_ID_BYTES_CHANGED');
  assert.equal(observed.nonce, exact.nonce, 'NONCE_BYTES_CHANGED');
  assert.deepEqual(observed.workspace_ids, exact.workspace_ids, 'WORKSPACE_SELECTION_CHANGED');
  assert.deepEqual(observed, exact);
  assert.deepEqual(decodeTicket(legacy(exact)), exact);
});

test('leading Unicode BOM belongs to the device name and is preserved', () => {
  const exact = { ...ticket, inviter: { ...ticket.inviter, name: '\ufeffJim’s phone' } };
  assert.deepEqual(decodeTicket(encodeTicket(exact)), exact, 'DEVICE_NAME_BOM_REMOVED');
  assert.deepEqual(decodeTicket(legacy(exact)), exact);
});

test('ill-formed UTF16 names are rejected instead of silently changing identity labels', () => {
  for (const name of ['Jim \ud800', 'Jim \udfff', '\ud800x\udfff']) {
    const malformed = { ...ticket, inviter: { ...ticket.inviter, name } };
    assert.throws(() => encodeTicket(malformed), undefined, 'ILL_FORMED_DEVICE_NAME_ACCEPTED');
    assert.throws(() => decodeTicket(legacy(malformed)), undefined, 'ILL_FORMED_LEGACY_NAME_ACCEPTED');
  }
});

test('compact decoder rejects duplicate selected workspaces', () => {
  assert.throws(() => decodeTicket(compactText(compactRecord({ selected: ['launch', 'launch'] }))), undefined, 'DUPLICATE_SCOPE_ACCEPTED');
});

test('compact decoder rejects unsupported versions and invalid scope lengths or identities', () => {
  for (const options of [
    { version: 1 }, { version: 3 }, { selected: [] },
    { selected: Array.from({ length: 33 }, (_, i) => 'space_' + i) },
    { selected: [''] }, { selected: ['a'.repeat(129)] }, { selected: ['../private'] }, { selected: ['two spaces'] },
  ]) assert.throws(() => decodeTicket(compactText(compactRecord(options))));
});

test('compact decoder rejects expiry outside the exact integer range', () => {
  assert.throws(() => decodeTicket(compactText(compactRecord({ expiry: 9007199254740992n }))), undefined, 'UNSAFE_EXPIRY_ACCEPTED');
  assert.throws(() => decodeTicket(compactText(compactRecord({ expiry: 18446744073709551615n }))), undefined, 'UNSAFE_EXPIRY_ACCEPTED');
  const zero = { ...ticket, expires_at_ms: 0 };
  assert.deepEqual(decodeTicket(encodeTicket(zero)), zero); // Expiration policy remains with the authenticating host.
});

test('compact text rejects invalid UTF8 instead of replacing malformed bytes', () => {
  for (const name of [Buffer.from([0xc3, 0x28]), Buffer.from([0xed, 0xa0, 0x80]), Buffer.from([0xf0, 0x9f])]) {
    assert.throws(() => decodeTicket(compactText(compactRecord({ name }))), undefined, 'INVALID_UTF8_NAME_ACCEPTED');
  }
  for (const routes of [Buffer.from([0xff]), Buffer.from('not-json'), Buffer.from('{}')]) {
    assert.throws(() => decodeTicket(compactText(compactRecord({ routes }))));
  }
});

test('noncanonical base64 aliases and raw padding are rejected', () => {
  let raw = compactRecord();
  if (raw.length % 3 === 0) raw = compactRecord({ name: Buffer.from('Jim!') });
  const canonical = raw.toString('base64url'), alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const alias = canonical.slice(0, -1) + alphabet[alphabet.indexOf(canonical.at(-1)) ^ 1];
  assert.deepEqual(Buffer.from(alias, 'base64url'), raw, 'Fixture must change only unused base64 bits.');
  assert.throws(() => decodeTicket('w2.' + alias), undefined, 'NONCANONICAL_ENCODING_ACCEPTED');
  assert.throws(() => decodeTicket('w2.' + canonical + '='));
  assert.throws(() => decodeTicket('w2.' + canonical + '\n'));
});

test('legacy validation cannot add fields, change identity shape, or change scope shape', () => {
  for (const altered of [
    { ...ticket, extra: true },
    { ...ticket, inviter: { ...ticket.inviter, secret: 'not-a-field' } },
    { ...ticket, inviter: { ...ticket.inviter, address: { ...ticket.inviter.address, id: '12'.repeat(31) } } },
    { ...ticket, nonce: 'A7'.repeat(32) },
    { ...ticket, workspace_ids: ['launch', 'launch'] },
    { ...ticket, workspace_ids: 'launch' },
    { ...ticket, expires_at_ms: -1 }, { ...ticket, expires_at_ms: 1.5 },
  ]) assert.throws(() => decodeTicket(legacy(altered)));
  const missing = { ...ticket }; delete missing.nonce;
  assert.throws(() => decodeTicket(legacy(missing)));
});

test('compact routing stays bounded and invitation links cannot hide duplicate invite fields', () => {
  assert.throws(() => decodeTicket(compactText(compactRecord({ routes: Buffer.from(JSON.stringify(Array.from({ length: 17 }, () => ({})))) }))));
  assert.throws(() => decodeTicket(compactText(compactRecord({ routes: Buffer.from(JSON.stringify(['a'.repeat(4096)])) }))));
  const value = encodeTicket(ticket);
  assert.throws(() => decodeTicket(base + '#invite=' + value + '&%69nvite=' + value));
  assert.throws(() => decodeTicket(base + '?invite=' + value));
  assert.throws(() => decodeTicket(base + 'x'.repeat(15000) + '#invite=' + value));
});
