/**
 * Renderer side of the mini Unity Hub. Every call goes through the main process (hub-* invoke
 * channels, main/background.ts "MINI UNITY HUB"): finding editors and templates, picking the parent
 * folder, creating and launching a project. Outside Electron (tests, a plain browser) there is no
 * `window.ipc`; reads answer empty and actions answer an error without throwing.
 */

export interface HubEditor { version: string; path: string }
export interface HubTemplate { id: string; label: '2d' | '3d' | 'hdrp' | 'other'; file: string }
export type HubPhase = 'starting' | 'running' | 'done' | 'failed';
export interface HubProgress { phase: HubPhase; elapsedMs: number; logTail?: string }
export type HubCreateResult = { ok: true; path: string } | { ok: false; error: string };

type Ipc = { invoke: (channel: string, ...a: unknown[]) => Promise<any>; on?: (channel: string, cb: (...a: unknown[]) => void) => () => void };

const ipc = (): Ipc | null => {
  const w = typeof window !== 'undefined' ? (window as any) : null;
  return w?.ipc?.invoke ? w.ipc : null;
};

const call = async (channel: string, arg?: unknown): Promise<any> => {
  const channelIpc = ipc();
  if (!channelIpc) return null;
  try {
    return arg === undefined ? await channelIpc.invoke(channel) : await channelIpc.invoke(channel, arg);
  } catch {
    return null;
  }
};

/** The engine's rule (unity-create.ts NAME_PATTERN), checked here only to explain it inline. */
export const PROJECT_NAME_RULE = /^[\p{L}\p{N} _-]{1,64}$/u;
export type NameProblem = 'empty' | 'chars' | 'long' | 'spaces' | null;
export const nameProblem = (name: string): NameProblem => {
  if (!name) return 'empty';
  if ([...name].length > 64) return 'long';
  if (name !== name.trim()) return 'spaces';
  return PROJECT_NAME_RULE.test(name) ? null : 'chars';
};

/** null = the call failed (no desktop bridge or an error); [] = it answered with no editors. */
export async function listEditors(): Promise<HubEditor[] | null> {
  const res = await call('hub-list-editors');
  if (!res || !Array.isArray(res.editors)) return null;
  return res.editors.filter((e: any) => e && typeof e.version === 'string' && typeof e.path === 'string');
}

export async function listTemplates(version: string): Promise<HubTemplate[]> {
  const res = await call('hub-list-templates', { version });
  if (!res || !Array.isArray(res.templates)) return [];
  return res.templates.filter((t: any) => t && typeof t.id === 'string' && typeof t.file === 'string');
}

export async function pickParentDir(): Promise<string | null> {
  const res = await call('hub-pick-parent-dir');
  return res && typeof res.path === 'string' && res.path ? res.path : null;
}

export async function createProject(arg: { parentDir: string; name: string; version: string; templateId?: string }): Promise<HubCreateResult> {
  const res = await call('hub-create-project', arg);
  if (res && res.ok === true && typeof res.path === 'string') return { ok: true, path: res.path };
  return { ok: false, error: res && typeof res.error === 'string' ? res.error : '' };
}

export async function cancelCreate(): Promise<boolean> {
  return !!(await call('hub-cancel-create'))?.cancelled;
}

/** Launches the Unity editor on a trusted project; the error is the engine's own text. */
export async function launchProject(path: string, version?: string): Promise<{ opened: boolean; error?: string }> {
  const res = await call('hub-open-project', version ? { path, version } : { path });
  if (res && res.opened === true) return { opened: true };
  return { opened: false, error: res && typeof res.error === 'string' ? res.error : undefined };
}

/** Subscribes to creation progress; returns the unsubscribe. */
export function onCreateProgress(cb: (p: HubProgress) => void): () => void {
  const channelIpc = ipc();
  if (!channelIpc?.on) return () => {};
  return channelIpc.on('hub-create-progress', (p: any) => {
    if (p && typeof p.phase === 'string' && typeof p.elapsedMs === 'number') cb(p as HubProgress);
  });
}

/** The engine reports a user cancel as the bare error text 'cancelled' (unity-create.ts). */
export const isCancelled = (error: string) => error === 'cancelled';

/**
 * A short name for a template file id like "com.unity.template.universal-3d-17.0.2":
 * Unity names URP templates "universal" and HDRP "hd"/"high-end"; the version tail is dropped.
 */
export function templateName(t: HubTemplate): string {
  const slug = t.id.replace(/^com\.unity\.template\./, '').replace(/-\d+(\.\d+)*(-[\w.]+)?$/, '');
  if (t.label === '2d') return '2D';
  if (t.label === '3d') return '3D';
  if (t.label === 'hdrp') return 'HDRP';
  const urp = /^(universal|urp)(-(.*))?$/.exec(slug);
  if (urp) return `URP${urp[3] ? ` ${urp[3].toUpperCase()}` : ''}`;
  return slug.split(/[-_.]/).filter(Boolean).map(w => (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join(' ');
}

/** Template order on the picker: 2D, 3D, URP, HDRP, then the rest by name. */
export function sortTemplates(list: HubTemplate[]): HubTemplate[] {
  const rank = (t: HubTemplate) => (t.label === '2d' ? 0 : t.label === '3d' ? 1 : /URP/.test(templateName(t)) ? 2 : t.label === 'hdrp' ? 3 : 4);
  return [...list].sort((a, b) => rank(a) - rank(b) || templateName(a).localeCompare(templateName(b)));
}

/** mm:ss of the creation clock. */
export const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

/** Parent folder + name as the engine will join them, in the parent's own separator style. */
export const joinTarget = (parent: string, name: string) => {
  const sep = /\\/.test(parent) && !/\//.test(parent) ? '\\' : '/';
  return `${parent.replace(/[\\/]+$/, '')}${sep}${name}`;
};
