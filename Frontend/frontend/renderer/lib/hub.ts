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
  parentNotPicked: 'hub.error.parentNotPicked', templateUnknown: 'hub.error.templateUnknown', openExited: 'hub.error.openExited',
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
export async function launchProject(path: string, version?: string): Promise<{ opened: boolean; alreadyOpen?: boolean; error?: string } & HubFailureDetails> {
  const res = await call('hub-open-project', version ? { path, version } : { path });
  if (res && res.opened === true) return res.alreadyOpen === true ? { opened: true, alreadyOpen: true } : { opened: true };
  return { opened: false, error: res && typeof res.error === 'string' ? res.error : undefined, ...failureDetails(res) };
}

// Launches whose IPC call is still awaiting, by project: a second press joins the first instead of
// asking main for another editor.
const pendingLaunches = new Map<string, Promise<boolean>>();

/** Launches and toasts the outcome; every Launch Unity button goes through here. Resolves whether Unity is (being) opened. */
export function launchWithToast(
  path: string, version: string | undefined,
  showToast: (message: string, type: 'success' | 'error' | 'info') => void,
  t: (key: TKey, values?: TValues) => string,
): Promise<boolean> {
  const key = path.replace(/[\\/]+$/, '').replace(/\\/g, '/').toLowerCase();
  const pending = pendingLaunches.get(key);
  if (pending) return pending;
  const run = (async () => {
    try {
      const res = await launchProject(path, version);
      if (res.opened) showToast(res.alreadyOpen ? t('hub.alreadyOpen') : t('hub.launched'), res.alreadyOpen ? 'info' : 'success');
      else showToast(t('hub.launchFailed', { error: hubFailureText(res, t) }), 'error');
      return res.opened;
    } finally {
      pendingLaunches.delete(key);
    }
  })();
  pendingLaunches.set(key, run);
  return run;
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

// --- Installing a Unity editor (hub-install-* channels; the main side drives Unity's own `unity` CLI) ---
// These types mirror the main<->renderer install contract field for field.

export type CliState =
  | { available: true }
  | { available: false; reason: 'noHub' }
  | { available: false; reason: 'openHubOnce' };
export interface InstallEula { label: string; url: string }
export interface InstallModule {
  id: string; name: string; category: string;
  downloadBytes: number; installedBytes: number;
  preSelected: boolean;
  eulas: InstallEula[];
  parts: { id: string; name: string; downloadBytes: number }[];
  installed: boolean;
}
export interface InstallRelease {
  version: string; changeset: string;
  stream: 'LTS' | 'SUPPORTED';
  releaseDate: string;
  recommended: boolean;
  installed: boolean;
  downloadBytes: number; installedBytes: number;
  modules: InstallModule[];
}
export type CatalogResult =
  | { ok: true; cli: CliState; releases: InstallRelease[]; requestedFound?: boolean }
  | { ok: false; cli: CliState; code: 'offline' | 'catalogFailed' };
export type PlanErrorCode = 'invalidArguments' | 'unknownVersion' | 'unknownModule' | 'cliMissing';
export type PlanResult =
  | { ok: true; installRoot: string; downloadBytes: number; installedBytes: number; freeBytes: number | null; enough: boolean; eulas: InstallEula[] }
  | { ok: false; code: PlanErrorCode };
export type InstallErrorCode = 'busy' | 'cliMissing' | 'invalidArguments' | 'unknownVersion' | 'unknownModule'
  | 'eulaNotAccepted' | 'notEnoughSpace' | 'elevationRefused' | 'cancelled' | 'cliFailed' | 'exitCode';
export type InstallResult =
  | { ok: true; completed: string[] }
  | { ok: false; code: InstallErrorCode; message?: string; completed?: string[] };
export interface InstallProgress {
  phase: 'starting' | 'download' | 'install' | 'done' | 'failed';
  pct: number | null;
  message: string;
  stalled: boolean;
  retrying: boolean;
  elapsedMs: number;
}
export interface InstallRequest { version: string; modules: string[]; acceptedEulas: boolean }

const INSTALL_ERROR_KEYS: Record<InstallErrorCode, TKey> = {
  busy: 'hub.install.error.busy', cliMissing: 'hub.install.error.cliMissing', invalidArguments: 'hub.install.error.invalidArguments',
  unknownVersion: 'hub.install.error.unknownVersion', unknownModule: 'hub.install.error.unknownModule',
  eulaNotAccepted: 'hub.install.error.eulaNotAccepted', notEnoughSpace: 'hub.install.error.notEnoughSpace',
  elevationRefused: 'hub.install.error.elevationRefused', cancelled: 'hub.install.error.cancelled',
  cliFailed: 'hub.install.error.cliFailed', exitCode: 'hub.install.error.exitCode',
};
export const isInstallErrorCode = (c: unknown): c is InstallErrorCode =>
  typeof c === 'string' && Object.prototype.hasOwnProperty.call(INSTALL_ERROR_KEYS, c);

/** The plain reason for a failed install or plan; the CLI's own words ride along only for cliFailed. */
export function installFailureText(r: { code: InstallErrorCode; message?: string }, t: (key: TKey, values?: TValues) => string): string {
  const text = t(INSTALL_ERROR_KEYS[r.code] ?? 'hub.install.error.exitCode');
  return r.code === 'cliFailed' && r.message ? `${text} ${t('hub.install.cliSaid', { message: r.message })}` : text;
}

const isEulas = (v: unknown): v is InstallEula[] =>
  Array.isArray(v) && v.every(e => e && typeof e.label === 'string' && typeof e.url === 'string');
const isCli = (c: any): c is CliState =>
  c?.available === true || (c?.available === false && (c.reason === 'noHub' || c.reason === 'openHubOnce'));

/** null = no desktop bridge, or an answer that does not have the contract's shape. */
export async function loadInstallCatalog(requested?: string): Promise<CatalogResult | null> {
  const res = await call('hub-install-catalog', requested);
  if (!res || !isCli(res.cli)) return null;
  if (res.ok === true && Array.isArray(res.releases)) {
    return {
      ok: true, cli: res.cli, releases: res.releases.filter((r: any) => r && typeof r.version === 'string' && Array.isArray(r.modules)),
      ...(typeof res.requestedFound === 'boolean' ? { requestedFound: res.requestedFound } : {}),
    };
  }
  if (res.ok === false && (res.code === 'offline' || res.code === 'catalogFailed')) return { ok: false, cli: res.cli, code: res.code };
  return null;
}

/** null = no bridge or a malformed answer. */
export async function planInstall(arg: { version: string; modules: string[] }): Promise<PlanResult | null> {
  const res = await call('hub-install-plan', arg);
  if (res?.ok === true && typeof res.downloadBytes === 'number' && typeof res.installedBytes === 'number' && isEulas(res.eulas)) {
    return {
      ok: true, installRoot: typeof res.installRoot === 'string' ? res.installRoot : '', downloadBytes: res.downloadBytes,
      installedBytes: res.installedBytes, freeBytes: typeof res.freeBytes === 'number' ? res.freeBytes : null,
      enough: res.enough !== false, eulas: res.eulas,
    };
  }
  if (res?.ok === false && ['invalidArguments', 'unknownVersion', 'unknownModule', 'cliMissing'].includes(res.code)) return { ok: false, code: res.code };
  return null;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

/** Resolves when the install ends; a missing bridge or a malformed answer reads as the generic exitCode failure. */
export async function startInstall(arg: InstallRequest): Promise<InstallResult> {
  const res = await call('hub-install-start', arg);
  if (res?.ok === true) return { ok: true, completed: strings(res.completed) };
  return {
    ok: false, code: isInstallErrorCode(res?.code) ? res.code : 'exitCode',
    ...(typeof res?.message === 'string' && res.message ? { message: res.message } : {}),
    ...(Array.isArray(res?.completed) ? { completed: strings(res.completed) } : {}),
  };
}

export async function cancelInstall(): Promise<{ cancelled: boolean; reason?: 'installing' | 'idle' }> {
  const res = await call('hub-install-cancel');
  return { cancelled: res?.cancelled === true, ...(res?.reason === 'installing' || res?.reason === 'idle' ? { reason: res.reason } : {}) };
}

/** The existing open-unity-hub channel: Hub fetches its install tool on its first launch. */
export async function openUnityHub(): Promise<boolean> {
  return (await call('open-unity-hub'))?.opened === true;
}

/** Subscribes to install progress; returns the unsubscribe. */
export function onInstallProgress(cb: (p: InstallProgress) => void): () => void {
  const channelIpc = ipc();
  if (!channelIpc?.on) return () => {};
  return channelIpc.on('hub-install-progress', (p: any) => {
    if (p && typeof p.phase === 'string' && typeof p.elapsedMs === 'number') {
      cb({
        phase: p.phase, pct: typeof p.pct === 'number' ? p.pct : null, message: typeof p.message === 'string' ? p.message : '',
        stalled: p.stalled === true, retrying: p.retrying === true, elapsedMs: p.elapsedMs,
      });
    }
  });
}

/** What an install was started with, plus display names, so a remounted screen never needs the catalog again. */
export interface InstallTarget {
  req: InstallRequest;
  /** Adding modules to an installed editor rather than installing a new one. */
  addTo: boolean;
  /** module id -> name, to say which parts did install. */
  names: Record<string, string>;
}
export type InstallRun =
  | { state: 'idle' }
  | { state: 'installing'; target: InstallTarget; progress: InstallProgress; cancelling: boolean; cancelRefused: boolean; cancelIdle: boolean }
  | { state: 'done'; target: InstallTarget; progress: InstallProgress; completed: string[] }
  | { state: 'failed' | 'cancelled'; target: InstallTarget; progress: InstallProgress; code: InstallErrorCode; message?: string; completed: string[] };

/**
 * The install run, kept outside any component like the creation run: main runs one install at a
 * time and it outlives the panel, so a screen that remounts finds the run here. The store holds the
 * only hub-install-progress listener, and only while an install is active.
 */
let install: InstallRun = { state: 'idle' };
let stopInstallProgress: (() => void) | null = null;
const installWatchers = new Set<() => void>();
const setInstall = (next: InstallRun) => { install = next; installWatchers.forEach(w => w()); };
const dropInstallProgress = () => { stopInstallProgress?.(); stopInstallProgress = null; };
const START_PROGRESS: InstallProgress = { phase: 'starting', pct: null, message: '', stalled: false, retrying: false, elapsedMs: 0 };

export const getInstall = (): InstallRun => install;
export const subscribeInstall = (cb: () => void): (() => void) => {
  installWatchers.add(cb);
  return () => { installWatchers.delete(cb); };
};

export async function runInstall(target: InstallTarget): Promise<void> {
  if (install.state === 'installing') return;
  setInstall({ state: 'installing', target, progress: START_PROGRESS, cancelling: false, cancelRefused: false, cancelIdle: false });
  dropInstallProgress();
  stopInstallProgress = onInstallProgress(p => {
    if (install.state === 'installing') setInstall({ ...install, progress: p, cancelRefused: install.cancelRefused && p.phase === 'install', cancelIdle: false });
  });
  const res = await startInstall(target.req);
  dropInstallProgress();
  // Read the store again: progress events changed it while the install ran.
  const now = getInstall();
  const progress = now.state === 'idle' ? START_PROGRESS : now.progress;
  if (res.ok === true) setInstall({ state: 'done', target, progress, completed: res.completed });
  else {
    setInstall({
      state: res.code === 'cancelled' ? 'cancelled' : 'failed', target, progress, code: res.code,
      ...(res.message ? { message: res.message } : {}), completed: res.completed ?? [],
    });
  }
}

export async function cancelInstallRun(): Promise<void> {
  if (install.state !== 'installing') return;
  // Each run has its own target object: a late answer must not touch the next run.
  const { target } = install;
  setInstall({ ...install, cancelling: true });
  const res = await cancelInstall();
  // Refused: either the CLI is past the point where stopping works ('installing'), or main has no install to stop ('idle').
  if (!res.cancelled && install.state === 'installing' && install.target === target) {
    setInstall({ ...install, cancelling: false, cancelRefused: res.reason === 'installing', cancelIdle: res.reason !== 'installing' });
  }
}

/** Leaves a finished install; one in progress stays. */
export const clearInstall = () => { if (install.state !== 'installing') setInstall({ state: 'idle' }); };

/** Test hook: forgets any install and its listener. */
export const resetInstallStore = () => { dropInstallProgress(); setInstall({ state: 'idle' }); };

const numberLocale = (lang: string) => (lang === 'tr' ? 'tr-TR' : 'en-GB');

/** Bytes as MB or GB with one decimal, in the UI language's number format. */
export function formatSize(bytes: number, lang: string): string {
  const gb = bytes / 1024 ** 3;
  const big = gb >= 1;
  const n = new Intl.NumberFormat(numberLocale(lang), { minimumFractionDigits: 1, maximumFractionDigits: 1 })
    .format(big ? gb : Math.max(0, bytes) / 1024 ** 2);
  return `${n} ${big ? 'GB' : 'MB'}`;
}

export const formatPct = (pct: number, lang: string) =>
  new Intl.NumberFormat(numberLocale(lang), { style: 'percent', maximumFractionDigits: 0 }).format(Math.min(100, Math.max(0, pct)) / 100);

export const formatDate = (iso: string, lang: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat(numberLocale(lang), { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
};
