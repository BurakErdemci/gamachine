/**
 * Renderer side of computer use. Every call goes through the main process
 * (`computer-use` invoke channel, main/helpers/computer-use.ts): enabling,
 * answering a card and stopping need the UI secret, which page JS never holds.
 * Outside Electron (tests, a plain browser) there is no `window.ipc` and every
 * call answers `{ error: 'no_desktop' }` without throwing.
 */

import { isRemotePage } from './remote/transport';

export interface ComputerGrant { bundle_id: string; app_name: string; granted_at: string }

export interface ComputerUseStatus {
  available: boolean;
  running: boolean;
  /** Host OS (`process.platform`); drives which permission steps the page shows. */
  platform: string;
  settings: {
    enabled: boolean;
    active?: boolean;
    grants: ComputerGrant[];
    runtime?: { running: boolean; driver_path: string | null; socket_path: string | null };
    halted?: number[];
  };
  permissions: { accessibility: boolean; screen: string };
}

export type ComputerUseError = { error: string };

const ipc = (): { invoke: (...a: unknown[]) => Promise<unknown> } | null => {
  const w = typeof window !== 'undefined' ? (window as any) : null;
  return w?.ipc?.invoke ? w.ipc : null;
};

// The paired browser's `window.ipc` is the remote shim, and the backend refuses the
// `computer-use` channel over it: that page has no desktop computer-use IPC.
export const hasComputerUseIpc = (): boolean => ipc() !== null && !isRemotePage();

export const isComputerUseError = (v: unknown): v is ComputerUseError =>
  !!v && typeof v === 'object' && typeof (v as { error?: unknown }).error === 'string';

export async function computerUseCall(req: Record<string, unknown>): Promise<unknown> {
  const channel = ipc();
  if (!channel) return { error: 'no_desktop' };
  try {
    return await channel.invoke('computer-use', req);
  } catch (err) {
    return { error: (err as Error)?.message || 'ipc_failed' };
  }
}

/** The host's status answer, or null when it is malformed or an error. */
export const parseComputerUseStatus = (v: unknown): ComputerUseStatus | null => {
  if (!v || typeof v !== 'object' || isComputerUseError(v)) return null;
  const s = v as any;
  if (typeof s.available !== 'boolean' || !s.settings || typeof s.settings !== 'object'
    || typeof s.settings.enabled !== 'boolean' || !s.permissions || typeof s.permissions !== 'object') return null;
  const grants = Array.isArray(s.settings.grants)
    ? s.settings.grants.filter((g: any) => g && typeof g.bundle_id === 'string' && typeof g.app_name === 'string')
    : [];
  return {
    available: s.available,
    running: s.running === true,
    platform: typeof s.platform === 'string' ? s.platform : 'darwin',
    settings: { ...s.settings, grants },
    permissions: {
      accessibility: s.permissions.accessibility === true,
      screen: typeof s.permissions.screen === 'string' ? s.permissions.screen : 'not-determined',
    },
  };
};

export async function computerUseStatus(): Promise<ComputerUseStatus | ComputerUseError> {
  const raw = await computerUseCall({ op: 'status' });
  return parseComputerUseStatus(raw) ?? (isComputerUseError(raw) ? raw : { error: 'malformed' });
}

export const setComputerUseEnabled = (on: boolean) => computerUseCall({ op: on ? 'enable' : 'disable' });
export const requestAccessibility = () => computerUseCall({ op: 'request-accessibility' });
export const openScreenSettings = () => computerUseCall({ op: 'open-screen-settings' });
export const restartComputerUse = () => computerUseCall({ op: 'restart' });
export const removeComputerGrant = (bundleId: string) => computerUseCall({ op: 'remove-grant', bundleId });

/** Answer a computer_* card with the UI secret; `remember` = "Always" for that app. */
export const answerComputerCard = (gateId: string, approved: boolean, remember: boolean) =>
  computerUseCall({ op: 'answer-card', gateId, approved, remember });

/**
 * Stop computer use for the rest of this conversation's turn (backend
 * `POST /computer-use/halt/{id}`). The user's next message lifts it.
 */
export async function haltComputerUse(conversationId: number) {
  return computerUseCall({ op: 'halt', conversationId });
}

/**
 * Code editors are allowed apps, but their built-in terminal runs whatever is
 * typed into it without a card (owner decision, 15 Oct 2026), so their card
 * says so.
 */
const EDITOR_IDS = new Set([
  'com.microsoft.vscode', 'com.microsoft.vscodeinsiders', 'com.vscodium',
  'com.todesktop.230313mzl4w4u92', // Cursor
  'com.google.android.studio',
]);
export const isCodeEditorBundle = (bundleId: unknown): boolean => {
  if (typeof bundleId !== 'string') return false;
  const id = bundleId.toLowerCase();
  return EDITOR_IDS.has(id) || id.startsWith('com.jetbrains.');
};
