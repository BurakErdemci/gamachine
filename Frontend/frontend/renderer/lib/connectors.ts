/**
 * Renderer side of the connectors (Settings > Bağlayıcılar). Every call goes through the main
 * process over the single `connectors` IPC channel; the shapes mirror CONTRACT.md section 3.
 */

export type ConnectorStateId = 'app_missing' | 'app_too_old' | 'not_installed' | 'installing' | 'ready' | 'update_available' | 'error';
export type StepId = 'python' | 'package' | 'addon' | 'check';
export const INSTALL_STEPS: readonly StepId[] = ['python', 'package', 'addon', 'check'];

export interface Bilingual { tr: string; en: string }
export interface AppVersion { version: string; path: string }

export interface ConnectorView {
  id: string;
  name: string;
  summary: Bilingual;
  capabilities: { tr: string[]; en: string[] };
  risk: Bilingual;
  state: ConnectorStateId;
  enabled: boolean;
  connected: boolean;
  app: {
    found: AppVersion[];
    chosen: AppVersion | null;
    custom_path: string | null;
    min_version: string;
    download_url: string;
  };
  installed: { version: string; addon_path?: string; addon_owner?: 'gamachine' | 'user' } | null;
  error: { code: string; message: string } | null;
}

export interface InstallJob {
  id: string;
  state: 'running' | 'done' | 'failed' | 'cancelled';
  step: StepId;
  steps_done: StepId[];
  downloaded_mb: number | null;
  started_at: number | string;
  error: { code: string; message: string; retryable: boolean } | null;
}

export interface InstallPlan {
  download_mb: number;
  estimated_s: number;
  target: string;
  steps: StepId[];
  /** Another tool's incompatible Blender add-on that the install would back up and replace. */
  replace_addon?: { path: string; found_protocol: number; needed_protocol: number } | null;
}

export type Reply<T> = { ok: true; data: T } | { ok: false; error: string; status?: number };

type Ipc = { invoke: (channel: string, ...a: unknown[]) => Promise<any> };

/** True when the app runs inside the desktop shell (the only place the backend routes are reachable). */
export const connectorsAvailable = (): boolean => typeof window !== 'undefined' && !!(window as any).ipc?.invoke;

async function call<T>(op: string, id?: string, body?: unknown, ok: (v: any) => boolean = () => true): Promise<Reply<T>> {
  const ipc = typeof window !== 'undefined' ? ((window as any).ipc as Ipc | undefined) : undefined;
  if (!ipc?.invoke) return { ok: false, error: 'no_desktop' };
  try {
    const r = await ipc.invoke('connectors', { op, id, body });
    if (r && typeof r === 'object' && typeof r.error === 'string') {
      return { ok: false, error: r.error, status: typeof r.status === 'number' ? r.status : undefined };
    }
    if (!ok(r)) return { ok: false, error: 'bad_reply' };
    return { ok: true, data: r as T };
  } catch {
    return { ok: false, error: 'ipc_failed' };
  }
}

const isView = (v: any) => !!v && typeof v === 'object' && typeof v.id === 'string' && typeof v.state === 'string';

export const listConnectors = () => call<{ connectors: ConnectorView[] }>('list', undefined, undefined, v => Array.isArray(v?.connectors));
export const planInstall = (id: string) => call<InstallPlan>('plan', id, undefined, v => typeof v?.download_mb === 'number' && typeof v?.estimated_s === 'number');
/** `replaceAddon` is true only after the user agreed to it in the consent dialog. */
export const startInstall = (id: string, replaceAddon = false) =>
  call<{ job: InstallJob }>('install', id, replaceAddon ? { replace_addon: true } : undefined, v => !!v?.job);
export const installStatus = (id: string) => call<{ job: InstallJob | null }>('install_status', id, undefined, v => v && 'job' in v);
export const cancelInstall = (id: string) => call<{ cancelled: boolean }>('cancel', id);
export const removeConnector = (id: string) => call<ConnectorView>('remove', id, undefined, isView);
export const setConnectorEnabled = (id: string, enabled: boolean) => call<ConnectorView>(enabled ? 'enable' : 'disable', id, undefined, isView);
/** `path` null = detect the app automatically again. */
export const setAppPath = (id: string, path: string | null) => call<ConnectorView>('set_app_path', id, { path }, isView);
/** Opens the native file dialog in main; `{ cancelled: true }` when the user closes it. */
export const chooseAppPath = (id: string) => call<ConnectorView | { cancelled: true }>('choose_app_path', id);
/** Opens the app's download page (main checks the address; the renderer never passes one). */
export const openDownload = (id: string) => call<{ opened: boolean }>('open_download', id);

/** Epoch seconds, epoch milliseconds or an ISO date, as a millisecond timestamp (NaN when unreadable). */
export function startedAtMs(value: number | string | undefined): number {
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  if (typeof value === 'string') return Date.parse(value);
  return NaN;
}

/** "m:ss" for a duration in seconds. */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
