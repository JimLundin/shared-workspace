import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeSnapshot, decodeSurface, isNoteRevision, isSurfaceRevision, validateSurfaceUrl } from './protocol.ts';
import { createCodeDocument, createWebSurface, surfacePreset, surfacePresets, surfacePresentation } from './surfaces.ts';

test('legacy notes and typed surfaces decode together without inventing payload fields', () => {
  const identity = { id: 'revision', author: 'device', title: 'Resource', parents: [] };
  const snapshot = decodeSnapshot({
    device_id: 'device', device_name: 'Browser', operation_count: 4, workspaces: [], network: { type: 'active' }, peers: [],
    resources: [
      { id: 'note', workspace_id: 'work', heads: [{ ...identity, body: 'Saved note' }], history: [] },
      { id: 'code', workspace_id: 'work', heads: [{ ...identity, surface: { type: 'code_document', language: 'typescript', text: 'const answer = 42;' } }], history: [] },
      { id: 'word', workspace_id: 'work', heads: [{ ...identity, surface: { type: 'document', application: 'word', url: 'https://word.cloud.microsoft/documents/brief' } }], history: [] },
    ],
  });
  const note = snapshot.resources[0]!.heads[0]!, code = snapshot.resources[1]!.heads[0]!;
  assert.ok(isNoteRevision(note));
  assert.ok(isSurfaceRevision(code));
  assert.equal(note.body, 'Saved note');
  assert.equal('surface' in note, false);
  assert.equal('body' in code, false);
  assert.ok(Object.isFrozen(code.surface));
  for (const invalid of [{ ...identity }, { ...identity, body: '', surface: code.surface }]) {
    assert.throws(() => decodeSnapshot({ ...snapshot, resources: [{ id: 'bad', workspace_id: 'work', heads: [invalid], history: [] }] }));
  }
});

test('surface URL validation rejects script/data/file targets, credentials and ambiguous input', () => {
  for (const value of [
    'javascript:alert(1)', 'data:text/html,<p>x</p>', 'file:///tmp/file', 'http://example.org/',
    'https://user:password@example.org/', 'https://exam\nple.org/', 'https://example.org\\@other.org/',
    'https://example.org/a b', ' https://example.org/', 'https://example.org/' + 'x'.repeat(8192),
  ]) assert.throws(() => validateSurfaceUrl(value), value);
  assert.equal(validateSurfaceUrl('https://example.org/document?q=read#page2'), 'https://example.org/document?q=read#page2');
  assert.equal(createWebSurface('document', 'website', ' https://example.org/document ').type, 'document');
});

test('surface variants require their own complete fields and reject unknown or mixed payloads', () => {
  for (const invalid of [
    { type: 'application', application: 'word' },
    { type: 'application', application: 'unknown', url: 'https://example.org/' },
    { type: 'code_document', language: 'unknown', text: '' },
    { type: 'code_document', language: 'python', text: '', url: 'https://example.org/' },
    { type: 'document', application: 'word', url: 'https://example.org/', text: '' },
    { type: 'code_document', language: 'python', text: 'x'.repeat(128 * 1024 + 1) },
    { type: 'code_document', language: 'python', text: 'é'.repeat(128 * 1024) },
    { type: 'code_document', language: 'python', text: 'a\0b' },
  ]) assert.throws(() => decodeSurface(invalid));
  assert.deepEqual(createCodeDocument('python', 'print(42)\n'), { type: 'code_document', language: 'python', text: 'print(42)\n' });
});

test('presets keep real code editing separate from external vendor applications', () => {
  assert.equal(new Set(surfacePresets.map(preset => preset.id)).size, surfacePresets.length);
  assert.equal(surfacePresentation(surfacePreset('code')!.surface).type, 'code_editor');
  for (const id of ['word', 'excel', 'powerpoint', 'vscode']) {
    const preset = surfacePreset(id)!;
    assert.deepEqual(decodeSurface(preset.surface), preset.surface);
    const presentation = surfacePresentation(preset.surface);
    assert.equal(presentation.type, 'external');
    if (presentation.type === 'external') assert.match(presentation.url, /^https:\/\//);
  }
  assert.equal(surfacePreset('unknown'), null);
});
