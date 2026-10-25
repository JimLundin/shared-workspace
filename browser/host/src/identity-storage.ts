/** Verify current physical Turso files contain no superseded plaintext identity. */
import { connectionError } from './connections.ts';
import { requireThat } from './types.ts';

type FileHandle = Pick<FileSystemFileHandle, 'getFile'>;
type Directory = Readonly<{ getFileHandle(name: string): Promise<FileHandle> }>;

function prefixTable(pattern: Uint8Array): Uint8Array {
  const prefix = new Uint8Array(pattern.length);
  for (let index = 1, matched = 0; index < pattern.length; index++) {
    while (matched && pattern[index] !== pattern[matched]) matched = prefix[matched - 1]!;
    if (pattern[index] === pattern[matched]) matched++;
    prefix[index] = matched;
  }
  return prefix;
}
async function containsIdentity(file: File, patterns: readonly Uint8Array[]): Promise<boolean> {
  const prefixes = patterns.map(prefixTable), matched = patterns.map(() => 0);
  const reader = file.stream().getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return false;
      for (const byte of value) {
        for (let index = 0; index < patterns.length; index++) {
          const pattern = patterns[index]!, prefix = prefixes[index]!;
          let length = matched[index]!;
          while (length && byte !== pattern[length]) length = prefix[length - 1]!;
          if (byte === pattern[length]) length++;
          if (length === pattern.length) { await reader.cancel(); return true; }
          matched[index] = length;
        }
      }
    }
  } finally { reader.releaseLock(); }
}

/** SQL is quiescent under the host's exclusive lock until this barrier passes. */
export async function verifyIdentityStorage(
  database: string,
  seed: Uint8Array,
  checkpoint: () => Promise<unknown>,
  directory: () => Promise<Directory> = () => navigator.storage.getDirectory(),
): Promise<void> {
  let encoded: Uint8Array | undefined;
  try {
    requireThat(/^[a-zA-Z0-9_-]{1,80}\.db$/.test(database) && seed.length === 32, 'Invalid identity protection scope.');
    await checkpoint();
    const root = await directory();
    encoded = new TextEncoder().encode(Array.from(seed, byte => byte.toString(16).padStart(2, '0')).join(''));
    // The pinned Turso OPFS adapter registers precisely these two file names.
    // Read existing handles only; never create, rename, or remove another file.
    for (const name of [database, `${database}-wal`]) {
      let handle: FileHandle;
      try { handle = await root.getFileHandle(name); }
      catch (error) {
        if (name !== database && error instanceof DOMException && error.name === 'NotFoundError') continue;
        throw error;
      }
      const file = await handle.getFile();
      requireThat(name !== database || file.size > 0, 'Retained database is missing.');
      if (await containsIdentity(file, [seed, encoded])) throw connectionError('Device identity protection could not be verified. No network endpoint was opened.');
    }
  } catch { throw connectionError('Device identity protection could not be verified. Close and reopen this tab before sharing connections.'); }
  finally { encoded?.fill(0); }
}
