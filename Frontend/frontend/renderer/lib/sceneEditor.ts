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
  range?: { min: number; max: number } | number[]; readonly: boolean; tooltip?: string;
}
export interface ComponentGroup {
  type: string; label: string; componentId: number | null; enabled: boolean | null; removable: boolean; fields: SceneField[];
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
