import { mountCodeEditor } from '../src/code-editor.ts';
import type { CodeEditor } from '../src/code-editor.ts';

declare global {
  interface Window {
    codeEditorFixture: CodeEditor & Readonly<{ changes(): readonly string[] }>;
  }
}

const parent = document.getElementById('editor');
if (!parent) throw new Error('Missing editor check element');
const changes: string[] = [];
const editor = mountCodeEditor(parent, 'const answer = 42;\nconsole.log(answer);\n', 'javascript', text => changes.push(text));
window.codeEditorFixture = Object.freeze({ ...editor, changes: () => [...changes] });
