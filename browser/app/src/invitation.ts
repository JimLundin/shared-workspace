const MAX_TICKET_BYTES = 12 * 1024;
/** A URL is an entry point, not a grant. The host validates the opaque ticket. */
export function invitationLink(base: string, ticket: string): string {
  // BrowserHost can itself return an HTTPS entry link. Keep one fragment layer.
  if (ticket.startsWith('https://')) ticket = readInvitation(new URL(ticket).hash) ?? ticket;
  const url = new URL(base); url.search = ''; url.hash = new URLSearchParams({ invite: ticket }).toString(); return url.href;
}
export function readInvitation(fragment: string): string | null {
  if (!fragment) return null;
  const parameters = new URLSearchParams(fragment.replace(/^#/, ''));
  const entries = parameters.getAll('invite');
  if (entries.length !== 1) return null;
  const ticket = entries[0]?.trim() ?? '';
  if (!ticket || new TextEncoder().encode(ticket).length > MAX_TICKET_BYTES || /[\p{Cc}]/u.test(ticket)) return null;
  return ticket;
}
export function consumeInvitation(location: Pick<Location, 'hash' | 'pathname' | 'search'>, history: Pick<History, 'replaceState'>): string | null {
  const ticket = readInvitation(location.hash);
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  return ticket;
}
