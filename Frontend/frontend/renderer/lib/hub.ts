/**
 * Renderer side of the mini Unity Hub. Every call goes through the main process (hub-* invoke
 * channels, main/background.ts "MINI UNITY HUB"): finding editors and templates, picking the parent
 * folder, creating and launching a project. Outside Electron (tests, a plain browser) there is no
 * `window.ipc`; reads answer empty and actions answer an error without throwing.
 */
import type { Cleanup, HubErrorCode } from '../../main/helpers/unity-create';
import type { TKey, TValues } from './i18n';

export interface HubEditor { version: string; path: string }
export interface HubTemplate { id: string; label: '2d' | '3d' | 'hdrp' | 'other'; file: string }
export type HubPhase = 'starting' | 'running' | 'done' | 'failed';
export interface HubProgress { phase: HubPhase; elapsedMs: number; logTail?: string }
export interface HubFailureDetails {
  code?: string;
  params?: Record<string, string | number>;
  cleanup?: Cleanup;
  path?: string;
}
export type HubCreateResult = { ok: true; path: string } | ({ ok: false; error: string } & HubFailureDetails);

const ERROR_KEYS: Record<HubErrorCode, TKey> = {
  busy: 'hub.error.busy', nameInvalid: 'hub.error.nameInvalid', parentMissing: 'hub.error.parentMissing',
  exists: 'hub.error.exists', pathTooLong: 'hub.error.pathTooLong', spawnFailed: 'hub.error.spawnFailed',
  exited: 'hub.error.exited', notCreated: 'hub.error.notCreated', cancelled: 'hub.error.cancelled',
  notProject: 'hub.error.notProject', notTrusted: 'hub.error.notTrusted', versionUnknown: 'hub.error.versionUnknown',
  editorMissing: 'hub.error.editorMissing', invalidArguments: 'hub.error.invalidArguments',
  parentNotPicked: 'hub.error.parentNotPicked', templateUnknown: 'hub.error.templateUnknown',
};

/** Translate at render time so a stored result follows a language change. */
export function hubFailureText(result: HubFailureDetails & { error?: string }, t: (key: TKey, values?: TValues) => string): string {
  const key = Object.prototype.hasOwnProperty.call(ERROR_KEYS, result.code) ? ERROR_KEYS[result.code as HubErrorCode] : undefined;
  const cancelled = result.code === 'cancelled' || isCancelled(result.error ?? '');
  if (cancelled) {
    if (result.cleanup === 'removed') {
      const bin = t(/Win/.test(navigator.platform || navigator.userAgent || '') ? 'welcome.binWin' : 'welcome.binOther');
      return t('hub.cancelledRemoved', { bin });
    }
    if (result.cleanup === 'removeFailed') return t('hub.cancelledRemoveFailed', { path: result.path ?? '' });
  }
  const text = key ? t(key, result.params) : cancelled ? t('hub.error.cancelled') : result.error || t('hub.failedUnknown');
  if (key || cancelled) {
    if (result.cleanup === 'notOurs' || result.cleanup === 'pathChanged') {
      return `${text} ${t(result.cleanup === 'notOurs' ? 'hub.cleanupNotOurs' : 'hub.cleanupPathChanged', { path: result.path ?? '' })}`;
    }
    if (result.cleanup === 'removeFailed') return `${text} ${t('hub.cleanupRemoveFailed', { path: result.path ?? '' })}`;
  }
  return text;
}

const failureDetails = (res: any): HubFailureDetails => ({
  ...(typeof res?.code === 'string' ? { code: res.code } : {}),
  ...(res?.params && typeof res.params === 'object' ? { params: res.params } : {}),
  ...(['removed', 'notOurs', 'removeFailed', 'pathChanged', 'none'].includes(res?.cleanup) ? { cleanup: res.cleanup } : {}),
  ...(typeof res?.path === 'string' ? { path: res.path } : {}),
});

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
  } catch (e) {
    // The thrown message travels as the answer's error so the UI can show it; readers that expect
    // another shape treat it like any failed answer.
    return { error: e instanceof Error ? e.message : typeof e === 'string' ? e : '' };
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

/** null = the call failed; [] = the version has no templates. */
export async function listTemplates(version: string): Promise<HubTemplate[] | null> {
  const res = await call('hub-list-templates', { version });
  if (!res || !Array.isArray(res.templates)) return null;
  return res.templates.filter((t: any) => t && typeof t.id === 'string' && typeof t.file === 'string');
}

export async function pickParentDir(): Promise<string | null> {
  const res = await call('hub-pick-parent-dir');
  return res && typeof res.path === 'string' && res.path ? res.path : null;
}

export async function createProject(arg: { parentDir: string; name: string; version: string; templateId?: string }): Promise<HubCreateResult> {
  const res = await call('hub-create-project', arg);
  if (res && res.ok === true && typeof res.path === 'string') return { ok: true, path: res.path };
  return { ok: false, error: res && typeof res.error === 'string' ? res.error : '', ...failureDetails(res) };
}

export async function cancelCreate(): Promise<boolean> {
  return !!(await call('hub-cancel-create'))?.cancelled;
}

/** Launches the Unity editor on a trusted project, retaining the failure's translation data. */
export async function launchProject(path: string, version?: string): Promise<{ opened: boolean; error?: string } & HubFailureDetails> {
  const res = await call('hub-open-project', version ? { path, version } : { path });
  if (res && res.opened === true) return { opened: true };
  return { opened: false, error: res && typeof res.error === 'string' ? res.error : undefined, ...failureDetails(res) };
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

export interface HubRequest { parentDir: string; name: string; version: string; templateId?: string }
/** What the run was started with; every non-idle state carries it so a card or a retry never reads the form. */
interface RunOf { name: string; req: HubRequest; template?: HubTemplate }
export type HubRun =
  | { state: 'idle' }
  | ({ state: 'creating'; progress: HubProgress; cancelling: boolean } & RunOf)
  | ({ state: 'done'; path: string; version: string } & RunOf)
  | ({ state: 'failed'; error: string } & HubFailureDetails & RunOf)
  | ({ state: 'cancelled'; error: string } & HubFailureDetails & RunOf);

/**
 * The creation run, kept outside any component: the engine runs one creation per process, so a
 * screen that remounts mid-run (or after it) finds the run here instead of an idle form. The store
 * holds the only hub-create-progress listener, and only while a run is active.
 */
let run: HubRun = { state: 'idle' };
let stopProgress: (() => void) | null = null;
const watchers = new Set<() => void>();
const setRun = (next: HubRun) => { run = next; watchers.forEach(w => w()); };
const dropProgress = () => { stopProgress?.(); stopProgress = null; };

export const getRun = (): HubRun => run;
export const subscribeRun = (cb: () => void): (() => void) => {
  watchers.add(cb);
  return () => { watchers.delete(cb); };
};

export async function startCreate(arg: HubRequest, template?: HubTemplate): Promise<void> {
  if (run.state === 'creating') return;
  const made = arg.name;
  const of: RunOf = { name: made, req: arg, template };
  setRun({ state: 'creating', ...of, progress: { phase: 'starting', elapsedMs: 0 }, cancelling: false });
  dropProgress();
  stopProgress = onCreateProgress(p => setRun(run.state === 'creating' ? { ...run, progress: p } : run));
  const res = await createProject(arg);
  dropProgress();
  if (res.ok === true) setRun({ state: 'done', ...of, path: res.path, version: arg.version });
  else if (res.code === 'cancelled' || isCancelled(res.error)) setRun({ state: 'cancelled', ...of, ...res });
  else setRun({ state: 'failed', ...of, ...res });
}

export async function cancelRun(): Promise<void> {
  if (run.state !== 'creating') return;
  setRun({ ...run, cancelling: true });
  const stopped = await cancelCreate();
  // Not stopped: Unity had already exited, so the result arrives on its own.
  if (!stopped && run.state === 'creating') setRun({ ...run, cancelling: false });
}

/** Leaves a finished run (done, failed, cancelled); a run in progress stays. */
export const clearRun = () => { if (run.state !== 'creating') setRun({ state: 'idle' }); };

/** Leaves `finished` only if it is still the run in the store; a late answer must not consume a newer run. */
export const clearRunIf = (finished: HubRun) => { if (run === finished) clearRun(); };

/** Test hook: forgets any run and its listener. */
export const resetHubStore = () => { dropProgress(); setRun({ state: 'idle' }); };
