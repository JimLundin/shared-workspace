import type { LocalDraft, LocalDraftJournal, LocalDraftReceipt, LocalDraftRemoval } from '../../../browser/host/src/local-drafts.ts';

export interface LocalDraftPort {
  listLocalDrafts(): Promise<LocalDraftJournal>;
  upsertLocalDraft(input: LocalDraft): Promise<LocalDraftReceipt>;
  removeLocalDraft(input: LocalDraftRemoval): Promise<LocalDraftReceipt>;
}

/** Owns ordered local retention. Shared state changes only through Host.command. */
export class RetainedInputJournal {
  readonly #port: LocalDraftPort;
  readonly #generations = new Map<string, number>();
  readonly #drafts = new Map<string, LocalDraft>();
  #queue: Promise<void> = Promise.resolve();
  readonly #errors = new Map<string, Error>();
  readonly #tasks = new Map<string, Promise<void>>();
  private constructor(port: LocalDraftPort, state: LocalDraftJournal) {
    this.#port = port;
    for (const draft of state.drafts) { this.#drafts.set(draft.id, draft); this.#generations.set(draft.id, draft.generation); }
    for (const fence of state.removed) this.#generations.set(fence.id, fence.generation);
  }
  static async open(port: LocalDraftPort): Promise<RetainedInputJournal> {
    return new RetainedInputJournal(port, await port.listLocalDrafts());
  }
  get failed(): boolean { return this.#errors.size > 0; }
  get pending(): boolean { return this.#tasks.size > 0; }
  entries(kind?: LocalDraft['kind']): readonly LocalDraft[] {
    return Object.freeze([...this.#drafts.values()].filter(draft => kind === undefined || draft.kind === kind));
  }
  read(id: string, kind: LocalDraft['kind']): string | null {
    const draft = this.#drafts.get(id);
    if (draft && draft.kind !== kind) throw new Error('The retained input has an unexpected destination.');
    return draft?.payload ?? null;
  }
  #generation(id: string): number {
    const next = (this.#generations.get(id) ?? 0) + 1;
    if (!Number.isSafeInteger(next)) throw new Error('The retained input version limit was reached.');
    this.#generations.set(id, next);
    return next;
  }
  #ordered(id: string, action: () => Promise<void>): Promise<void> {
    const next = this.#queue.then(action);
    this.#queue = next.then(() => undefined, () => undefined);
    this.#tasks.set(id, next);
    void next.then(() => {
      this.#errors.delete(id);
      if (this.#tasks.get(id) === next) this.#tasks.delete(id);
    }, () => {
      this.#errors.set(id, new Error('This input could not be retained on this device. Keep this window open and try saving again.'));
      if (this.#tasks.get(id) === next) this.#tasks.delete(id);
    });
    return next;
  }
  put(id: string, kind: LocalDraft['kind'], value: unknown): Promise<void> {
    const payload = JSON.stringify(value);
    const current = this.#drafts.get(id);
    if (current?.kind === kind && current.payload === payload && !this.#errors.has(id)) return this.#tasks.get(id) ?? Promise.resolve();
    const draft = Object.freeze({ id, kind, payload, generation: this.#generation(id) });
    // Cache the intended input immediately; the returned promise is its durability barrier.
    this.#drafts.set(id, draft);
    return this.#ordered(id, async () => {
      const receipt = await this.#port.upsertLocalDraft(draft);
      if (receipt.type !== 'retained' || receipt.id !== id || receipt.generation !== draft.generation) {
        throw new Error('A newer retained input needs review before continuing.');
      }
    });
  }
  remove(id: string): Promise<void> {
    const generation = this.#generation(id);
    this.#drafts.delete(id);
    return this.#ordered(id, async () => {
      const receipt = await this.#port.removeLocalDraft({ id, generation });
      if (receipt.type !== 'removed' || receipt.id !== id || receipt.generation !== generation) {
        throw new Error('Discarding this input was not confirmed.');
      }
    });
  }
  async flush(): Promise<void> { await this.#queue; if (this.failed) throw new Error('Some input could not be retained. Keep this window open and try saving it again.'); }
}
