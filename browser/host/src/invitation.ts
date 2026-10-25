import { bytes, deviceName, fields, hex, json, MAX_TICKET_BYTES, parse, requireThat, scope, type Ticket } from './types.ts';
import { number, string } from '../../../native/ui/src/protocol.ts';

const LEGACY = 'workspace-browser-v1.';
const COMPACT = 'w2.';
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

/** Decode the invitation's shape; the host still authenticates the peer and grant. */
function ticketRecord(value: unknown): Ticket {
  const record = fields(value, ['version', 'inviter', 'nonce', 'workspace_ids', 'expires_at_ms']);
  requireThat(record.version === 1, 'Unsupported invitation version.');
  const inviter = fields(record.inviter, ['name', 'address']);
  const address = fields(inviter.address, ['id', 'addrs']);
  requireThat(Array.isArray(address.addrs) && address.addrs.length <= 16 && bytes(json(address.addrs)) <= 4096, 'Invalid invitation routing information.');
  return {
    version: 1,
    inviter: { name: deviceName(inviter.name), address: { id: hex(address.id), addrs: address.addrs } },
    nonce: hex(record.nonce), workspace_ids: scope(record.workspace_ids), expires_at_ms: number(record.expires_at_ms),
  };
}

function base64(value: Uint8Array): string {
  return btoa(String.fromCharCode(...value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}
function unbase64(value: string): Uint8Array {
  requireThat(/^[A-Za-z0-9_-]+$/.test(value), 'Invalid invitation encoding.');
  const decoded = Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), char => char.charCodeAt(0));
  requireThat(base64(decoded) === value, 'Invitation encoding is not canonical.');
  return decoded;
}
function textFromLink(value: string): string {
  requireThat(bytes(value) <= MAX_TICKET_BYTES + 2048, 'Invitation is too large.');
  if (value.startsWith('https://')) {
    const fragments = new URLSearchParams(new URL(value).hash.slice(1)).getAll('invite');
    requireThat(fragments.length === 1, 'Invitation link must contain one invitation.');
    value = fragments[0]!;
  }
  requireThat(bytes(value) <= MAX_TICKET_BYTES, 'Invitation is too large.');
  return value;
}

function rawHex(value: string): Uint8Array {
  return Uint8Array.from(value.match(/../g)!, pair => Number.parseInt(pair, 16));
}
function hexText(value: Uint8Array): string {
  return Array.from(value, byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Bounded binary fields avoid JSON keys and hexadecimal expansion in QR codes. */
function pack(ticket: Ticket): Uint8Array {
  const expiry = new Uint8Array(8);
  new DataView(expiry.buffer).setBigUint64(0, BigInt(ticket.expires_at_ms));
  const parts: Uint8Array[] = [Uint8Array.of(2), rawHex(ticket.inviter.address.id), rawHex(ticket.nonce), expiry];
  function text(value: string, wide = false): void {
    const encoded = encoder.encode(value);
    parts.push(wide ? Uint8Array.of(encoded.length >>> 8, encoded.length & 255) : Uint8Array.of(encoded.length), encoded);
  }
  text(ticket.inviter.name);
  parts.push(Uint8Array.of(ticket.workspace_ids.length));
  for (const id of ticket.workspace_ids) text(id);
  text(json(ticket.inviter.address.addrs), true);
  const output = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
  let position = 0;
  for (const part of parts) { output.set(part, position); position += part.length; }
  return output;
}

function unpack(data: Uint8Array): Ticket {
  let position = 0;
  function take(count: number): Uint8Array {
    requireThat(position + count <= data.length, 'Invitation is incomplete.');
    const value = data.subarray(position, position + count); position += count; return value;
  }
  function byte(): number { return take(1)[0]!; }
  function text(wide = false): string {
    const length = wide ? (byte() << 8) | byte() : byte();
    return decoder.decode(take(length));
  }
  requireThat(byte() === 2, 'Unsupported compact invitation version.');
  const id = hexText(take(32)), nonce = hexText(take(32));
  const expiry = take(8);
  const expires_at_ms = number(Number(new DataView(expiry.buffer, expiry.byteOffset, expiry.byteLength).getBigUint64(0)));
  const name = text(), count = byte();
  requireThat(count > 0 && count <= 32, 'Select between 1 and 32 workspaces.');
  const workspace_ids = Array.from({ length: count }, () => text());
  const addrs = parse(text(true));
  requireThat(position === data.length, 'Invitation contains unexpected trailing data.');
  return ticketRecord({ version: 1, inviter: { name, address: { id, addrs } }, nonce, workspace_ids, expires_at_ms });
}

export function encodeTicket(input: Ticket, baseUrl?: string): string {
  const ticket = ticketRecord(input);
  const text = COMPACT + base64(pack(ticket));
  requireThat(bytes(text) <= MAX_TICKET_BYTES, 'Invitation is too large.');
  if (baseUrl) {
    const url = new URL(baseUrl);
    if (url.protocol === 'https:') {
      url.hash = new URLSearchParams({ invite: text }).toString();
      return url.href;
    }
  }
  return text;
}

export function decodeTicket(input: string): Ticket {
  const value = textFromLink(string(input));
  if (value.startsWith(COMPACT)) return unpack(unbase64(value.slice(COMPACT.length)));
  requireThat(value.startsWith(LEGACY), 'Invalid browser invitation.');
  return ticketRecord(parse(decoder.decode(unbase64(value.slice(LEGACY.length)))));
}
