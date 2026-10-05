import { useCallback, useEffect, useState } from 'react';

const KEY = 'app-scene-editor';
const EVENT = 'app-scene-editor-changed';

export function readSceneEditor(storage?: Pick<Storage, 'getItem'>): boolean {
  try { return (storage ?? localStorage).getItem(KEY) === 'on'; } catch { return false; }
}

export function writeSceneEditor(on: boolean, storage?: Pick<Storage, 'setItem'>): void {
  try { (storage ?? localStorage).setItem(KEY, on ? 'on' : 'off'); } catch { /* The live switch still works. */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT, { detail: on }));
}

export function useSceneEditorSetting(): readonly [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(false);
  useEffect(() => {
    setOn(readSceneEditor());
    const changed = (event: Event) => setOn((event as CustomEvent<boolean>).detail);
    const stored = (event: StorageEvent) => { if (event.key === KEY || event.key === null) setOn(readSceneEditor()); };
    window.addEventListener(EVENT, changed);
    window.addEventListener('storage', stored);
    return () => { window.removeEventListener(EVENT, changed); window.removeEventListener('storage', stored); };
  }, []);
  return [on, useCallback((value: boolean) => writeSceneEditor(value), [])];
}

export type PrefabKind = 'none' | 'root' | 'part' | 'missing';
export interface SceneNode {
  id: number; name: string; parentId: number | null; index: number;
  activeSelf: boolean; activeInHierarchy: boolean; childCount: number; prefab: PrefabKind; scene: string;
}
export interface SceneTree {
  epoch: string | number; version: number;
  scenes: { name: string; path: string; isDirty: boolean; isLoaded: boolean; isActive: boolean; rootIds: number[] }[];
  nodes: SceneNode[]; total: number; truncated: boolean;
}
export interface SceneField {
  path: string; label: string;
  kind: 'float' | 'int' | 'bool' | 'enum' | 'string' | 'vec2' | 'vec3' | 'vec4' | 'color' | 'ref' | 'mask' | 'list' | 'unsupported';
  value: unknown; options?: (string | { label: string; value: string | number })[];
  range?: { min: number; max: number } | number[]; readonly: boolean; tooltip?: string; truncated?: boolean;
}
export interface ComponentGroup {
  type: string; label: string; componentId: number | null; enabled: boolean | null; removable: boolean; locked?: boolean; fields: SceneField[];
}
export interface Inspection {
  node: { id: number; name: string; activeSelf: boolean; tag: string; layer: { index: number; name: string }; isStatic: boolean; prefab: PrefabKind; globalId: string };
  groups: ComponentGroup[]; truncated: boolean;
}
export interface SceneVersion {
  epoch: string | number; scene: number; selection: number; selectedId: number | null; playing: boolean; compiling: boolean;
  /** Structural and property counters; older Unity packages omit them, then `scene` stands in for both. */
  hierarchy?: number; props?: number;
}

/** One entry of Unity's GameObject create menu; `category` is "" for top-level items, `label` may hold "/" levels. */
export interface CreateMenuItem { item: string; category: string; label: string }
export interface ComponentMenuItem extends CreateMenuItem { present: boolean }
export type ComponentAction = 'reset' | 'remove' | 'up' | 'down';
export const inspectorWriteKey = {
  field: (id: number, path: string) => `field:${id}:${path}`,
  active: (id: number) => `active:${id}`,
  name: (id: number) => `name:${id}`,
  enabled: (id: number) => `enabled:${id}`,
  component: (id: number) => `component:${id}`,
  add: (id: number) => `add:${id}`,
};
export type SceneWriteCode =
  | 'locked' | 'compiling' | 'prefab_part' | 'invalid_name' | 'invalid_value' | 'invalid_item' | 'create_failed' | 'write_failed'
  | 'already_present' | 'required' | 'add_failed'
  | 'not_found' | 'unity_unavailable' | 'unity_timeout' | 'unity_error';
export type SceneWriteResult<T> = { ok: true; data: T } | { ok: false; code: SceneWriteCode };

const CONFLICTS = new Set(['locked', 'compiling', 'prefab_part', 'invalid_name', 'invalid_value', 'invalid_item', 'create_failed', 'write_failed', 'already_present', 'required', 'add_failed']);

export function writeErrorCode(status: number, detail: unknown): SceneWriteCode {
  if (status === 409) return typeof detail === 'string' && CONFLICTS.has(detail) ? detail as SceneWriteCode : 'write_failed';
  if (status === 404) return 'not_found';
  if (status === 503) return 'unity_unavailable';
  if (status === 504) return 'unity_timeout';
  return 'unity_error';
}

export async function postSceneWrite<T>(api: string, token: string, route: string, body: unknown): Promise<SceneWriteResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${api}/scene-editor/${route}`, {
      method: 'POST', headers: { 'X-Session-Token': token, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch { return { ok: false, code: 'unity_unavailable' }; }
  let data: unknown = null;
  try { data = await response.json(); } catch { /* An empty body still carries the status. */ }
  if (!response.ok) return { ok: false, code: writeErrorCode(response.status, (data as { detail?: unknown } | null)?.detail) };
  return { ok: true, data: data as T };
}

export async function fetchCreateMenu(api: string, token: string): Promise<CreateMenuItem[]> {
  const response = await fetch(`${api}/scene-editor/create-menu`, { headers: { 'X-Session-Token': token } });
  if (!response.ok) throw response.status;
  const data = await response.json() as { items?: unknown };
  // Entries that are not three strings are dropped; an empty result is treated as a failed load by the caller.
  const valid = (entry: unknown): entry is CreateMenuItem => !!entry && typeof entry === 'object'
    && ['item', 'category', 'label'].every(key => typeof (entry as Record<string, unknown>)[key] === 'string');
  return Array.isArray(data?.items) ? data.items.filter(valid) : [];
}

export async function fetchComponentMenu(api: string, token: string, id: number): Promise<ComponentMenuItem[]> {
  const response = await fetch(`${api}/scene-editor/component-menu/${id}`, { headers: { 'X-Session-Token': token } });
  if (!response.ok) throw response.status;
  const data = await response.json() as { items?: unknown };
  const valid = (entry: unknown): entry is ComponentMenuItem => !!entry && typeof entry === 'object'
    && ['item', 'category', 'label'].every(key => typeof (entry as Record<string, unknown>)[key] === 'string')
    && typeof (entry as Record<string, unknown>).present === 'boolean'
    && !!(entry as Record<string, unknown>).item && !!(entry as Record<string, unknown>).label;
  const items = Array.isArray(data?.items) ? data.items.filter(valid) : [];
  if (!items.length) throw new Error('Empty component menu');
  return items;
}
