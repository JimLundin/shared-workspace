import { isTauri, invoke } from '@tauri-apps/api/core';
import { checkPermissions, requestPermissions, scan, cancel, Format } from '@tauri-apps/plugin-barcode-scanner';
export interface ScannerPort { available(): Promise<boolean>; scan(): Promise<string>; cancel(): Promise<void> }
export class NativeScanner implements ScannerPort {
  async available(): Promise<boolean> { if (!isTauri()) return false; try { const value: unknown = await invoke('host_capabilities'); return value !== null && typeof value === 'object' && 'pairing_camera' in value && value.pairing_camera === true; } catch { return false; } }
  async scan(): Promise<string> {
    const permission = await checkPermissions();
    if (permission !== 'granted' && await requestPermissions() !== 'granted') throw new Error('Camera access was not granted. Paste an invitation instead.');
    const result = await scan({ cameraDirection: 'back', formats: [Format.QRCode], windowed: true });
    if (typeof result.content !== 'string' || !result.content.trim()) throw new Error('No invitation was read.');
    return result.content;
  }
  async cancel(): Promise<void> { await cancel(); }
}
