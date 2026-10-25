import { decodeSurface, validateSurfaceUrl } from './protocol.ts';
import type { CodeLanguage, Surface, WebApplication } from './protocol.ts';

export type SurfacePresetId = 'code' | Exclude<WebApplication, 'website'>;
export type SurfacePreset = Readonly<{
  id: SurfacePresetId;
  title: string;
  description: string;
  surface: Surface;
}>;

export const surfacePresets: readonly SurfacePreset[] = Object.freeze([
  Object.freeze({ id: 'code', title: 'Code document', description: 'Edit code here. Saved revisions travel with your workspace.', surface: decodeSurface({ type: 'code_document', language: 'plaintext', text: '' }) }),
  Object.freeze({ id: 'word', title: 'Word', description: 'Open Microsoft Word in a browser tab, or add an existing document link.', surface: decodeSurface({ type: 'application', application: 'word', url: 'https://word.cloud.microsoft/' }) }),
  Object.freeze({ id: 'excel', title: 'Excel', description: 'Open Microsoft Excel in a browser tab, or add an existing workbook link.', surface: decodeSurface({ type: 'application', application: 'excel', url: 'https://excel.cloud.microsoft/' }) }),
  Object.freeze({ id: 'powerpoint', title: 'PowerPoint', description: 'Open Microsoft PowerPoint in a browser tab, or add an existing presentation link.', surface: decodeSurface({ type: 'application', application: 'powerpoint', url: 'https://powerpoint.cloud.microsoft/' }) }),
  Object.freeze({ id: 'vscode', title: 'VS Code', description: 'Open the real web workbench in a browser tab.', surface: decodeSurface({ type: 'application', application: 'vscode', url: 'https://vscode.dev/' }) }),
  Object.freeze({ id: 'diagrams', title: 'Diagrams', description: 'Open the diagrams.net editor. Its files remain with the application.', surface: decodeSurface({ type: 'application', application: 'diagrams', url: 'https://app.diagrams.net/' }) }),
]);

export function surfacePreset(id: string): SurfacePreset | null {
  return surfacePresets.find(preset => preset.id === id) ?? null;
}

export function createWebSurface(type: 'application' | 'document', application: WebApplication, url: string): Surface {
  return decodeSurface({ type, application, url: validateSurfaceUrl(url.trim()) });
}

export function createCodeDocument(language: CodeLanguage = 'plaintext', text = ''): Surface {
  return decodeSurface({ type: 'code_document', language, text });
}

export function surfaceLabel(surface: Surface): string {
  switch (surface.type) {
    case 'code_document': return 'Code document';
    case 'application': case 'document':
      return surface.application === 'website' ? (surface.type === 'document' ? 'Web document' : 'Web application') : surfacePreset(surface.application)!.title;
  }
}

export type SurfacePresentation =
  | Readonly<{ type: 'code_editor'; language: CodeLanguage; text: string }>
  | Readonly<{ type: 'external'; url: string; label: string; reason: string }>;

/** Decide presentation locally; a shared target never grants application access. */
export function surfacePresentation(surface: Surface): SurfacePresentation {
  switch (surface.type) {
    case 'code_document': return Object.freeze({ type: 'code_editor', language: surface.language, text: surface.text });
    case 'application': case 'document': {
      const reason = surface.application === 'vscode'
        ? 'VS Code prevents embedding. Open its full workbench in a browser tab.'
        : surface.application === 'word' || surface.application === 'excel' || surface.application === 'powerpoint'
          ? 'Open this Microsoft application in a browser tab. Your account and document permissions stay with Microsoft.'
          : 'Open this application in a browser tab. The workspace shares its link; files and sign-in stay with the application.';
      return Object.freeze({ type: 'external', url: validateSurfaceUrl(surface.url), label: surfaceLabel(surface), reason });
    }
  }
}

export function surfacePreview(surface: Surface): string {
  switch (surface.type) {
    case 'code_document': return surface.text;
    case 'application': case 'document': return surface.url;
  }
}
