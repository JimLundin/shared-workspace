import { Annotation, Compartment, EditorState, Transaction } from '@codemirror/state';
import type { Extension } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLineGutter, highlightActiveLine, drawSelection } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { HighlightStyle, bracketMatching, syntaxHighlighting } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { json } from '@codemirror/lang-json';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { rust } from '@codemirror/lang-rust';
import { markdown } from '@codemirror/lang-markdown';
import type { CodeLanguage } from './protocol.ts';

function syntax(language: CodeLanguage): Extension {
  switch (language) {
    case 'javascript': return javascript();
    case 'typescript': return javascript({ typescript: true });
    case 'python': return python();
    case 'json': return json();
    case 'html': return html();
    case 'css': return css();
    case 'rust': return rust();
    case 'markdown': return markdown();
    case 'plaintext': return [];
  }
}

const highlighting = syntaxHighlighting(HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier, tags.operatorKeyword], color: 'var(--code-keyword)' },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: 'var(--code-string)' },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: 'var(--code-number)' },
  { tag: [tags.function(tags.variableName), tags.typeName, tags.className], color: 'var(--code-name)' },
  { tag: [tags.comment, tags.meta], color: 'var(--code-comment)', fontStyle: 'italic' },
  { tag: tags.heading, fontWeight: 'bold' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.strong, fontWeight: 'bold' },
  { tag: tags.invalid, textDecoration: 'underline wavy var(--code-invalid)' },
]));

export type CodeEditorTheme = 'light' | 'dark';
export type CodeEditor = Readonly<{
  focus(): void;
  read(): string;
  selection(): Readonly<{ from: number; to: number }>;
  setText(text: string): void;
  setLanguage(language: CodeLanguage): void;
  setReadonly(value: boolean): void;
  setTheme(theme: CodeEditorTheme): void;
  dispose(): void;
}>;

function theme(value: CodeEditorTheme): Extension {
  const dark = value === 'dark';
  return EditorView.theme({
    '&': {
      backgroundColor: 'var(--paper)', color: 'var(--ink)', fontSize: '16px',
      '--code-keyword': dark ? '#d8a8ff' : '#74409a',
      '--code-string': dark ? '#a3d8ab' : '#286b37',
      '--code-number': dark ? '#f3c38b' : '#91511b',
      '--code-name': dark ? '#9bc9ef' : '#215d89',
      '--code-comment': dark ? '#b4bdb6' : '#536659',
      '--code-invalid': dark ? '#ffada3' : '#a42c24',
    },
    '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', lineHeight: '1.7', overflow: 'auto' },
    '.cm-content': { minHeight: '280px', caretColor: 'var(--ink)' },
    '.cm-gutters': { backgroundColor: 'var(--soft)', color: 'var(--muted)', borderRight: '1px solid var(--line)' },
    '.cm-activeLine,.cm-activeLineGutter': { backgroundColor: 'var(--soft)' },
    '.cm-cursor': { borderLeftColor: 'var(--ink)' },
    '.cm-selectionBackground': { backgroundColor: 'var(--line) !important' },
    '&.cm-focused': { outline: '2px solid var(--accent)', outlineOffset: '2px' },
  }, { dark });
}

const observedText = Annotation.define<boolean>();
const editability = (readOnly: boolean): Extension => [
  EditorState.readOnly.of(readOnly),
  EditorView.editable.of(!readOnly),
  EditorView.contentAttributes.of({ 'aria-readonly': String(readOnly) }),
];

/**
 * Retain one editor per draft. Reconfigure it for observations and appearance;
 * dispose it when the owning draft changes identity. setText accepts an observed
 * version of that same document and never reports it as a user edit.
 */
export function mountCodeEditor(
  parent: HTMLElement,
  text: string,
  language: CodeLanguage,
  changed: (text: string) => void,
): CodeEditor {
  const mode = new Compartment(), editable = new Compartment(), appearance = new Compartment();
  let currentLanguage = language, readOnly = false, disposed = false;
  let currentTheme: CodeEditorTheme = parent.ownerDocument.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  const view = new EditorView({
    parent,
    state: EditorState.create({ doc: text, extensions: [
      lineNumbers(), highlightActiveLineGutter(), highlightActiveLine(), drawSelection(), history(), bracketMatching(), highlighting,
      // Tab leaves the editor. Ctrl/Cmd-[ and Ctrl/Cmd-] remain indent commands.
      keymap.of([...defaultKeymap, ...historyKeymap]),
      mode.of(syntax(language)), editable.of(editability(false)), appearance.of(theme(currentTheme)),
      EditorView.contentAttributes.of({ 'aria-label': 'Code content', 'aria-description': 'Tab moves to the next control. Use Control or Command with square brackets to change indentation.', spellcheck: 'false', tabindex: '0' }),
      EditorView.updateListener.of(update => {
        if (!disposed && update.transactions.some(transaction => transaction.docChanged && !transaction.annotation(observedText))) changed(update.state.doc.toString());
      }),
    ] }),
  });
  return Object.freeze({
    focus(): void { if (!disposed) view.focus(); },
    read(): string { return view.state.doc.toString(); },
    selection(): Readonly<{ from: number; to: number }> {
      const { from, to } = view.state.selection.main;
      return Object.freeze({ from, to });
    },
    setText(next: string): void {
      if (disposed) return;
      const previous = view.state.doc.toString();
      if (previous === next) return;
      let from = 0, previousEnd = previous.length, nextEnd = next.length;
      while (from < previousEnd && from < nextEnd && previous[from] === next[from]) from++;
      while (previousEnd > from && nextEnd > from && previous[previousEnd - 1] === next[nextEnd - 1]) { previousEnd--; nextEnd--; }
      view.dispatch({ changes: { from, to: previousEnd, insert: next.slice(from, nextEnd) }, annotations: [observedText.of(true), Transaction.addToHistory.of(false)] });
    },
    setLanguage(next: CodeLanguage): void {
      if (disposed || currentLanguage === next) return;
      currentLanguage = next;
      view.dispatch({ effects: mode.reconfigure(syntax(next)) });
    },
    setReadonly(next: boolean): void {
      if (disposed || readOnly === next) return;
      readOnly = next;
      const focused = view.hasFocus;
      view.dispatch({ effects: editable.reconfigure(editability(next)) });
      if (focused && !view.hasFocus) view.focus();
    },
    setTheme(next: CodeEditorTheme): void {
      if (disposed || currentTheme === next) return;
      currentTheme = next;
      view.dispatch({ effects: appearance.reconfigure(theme(next)) });
    },
    dispose(): void { if (!disposed) { disposed = true; view.destroy(); } },
  });
}
