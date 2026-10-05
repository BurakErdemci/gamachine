import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { InspectorPane } from '../renderer/components/home/InspectorPane';
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor';
import type { Inspection, SceneField } from '../renderer/lib/sceneEditor';

const field = (kind: SceneField['kind'], value: unknown, extra = {}): SceneField => ({ path: kind, label: kind, kind, value, readonly: false, ...extra });
let data: Inspection;
let editor: ReturnType<typeof useSceneEditor>;
let posts: { route: string; body: any }[];
let failure: string | null;
let held: Promise<void> | null;
let release: (() => void) | undefined;
let props: number;
let hierarchy: number;
let menuLoads: number;
let firstPresent: boolean;
let token = 0;
function Harness() {
  editor = useSceneEditor({ api: 'api', token: `fix-${token}`, editorOn: true, unityStatus: 'connected', hierarchyVisible: true, inspectorVisible: true });
  return <InspectorPane inspection={editor.inspection} loading={false} error={null} stale={editor.stale} actions={editor} />;
}
beforeEach(() => {
  vi.useFakeTimers(); token++; posts = []; failure = null; held = null; props = 1; hierarchy = 1; menuLoads = 0; firstPresent = true;
  data = { node: { id: 7, name: 'Cube', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: '' }, truncated: false,
    groups: [{ type: 'Transform', label: 'Transform', componentId: 10, enabled: null, removable: false, fields: [field('vec3', [-0.463420003, 1e-7, 3])] },
      { type: 'Behaviour', label: 'Behaviour', componentId: 20, enabled: true, removable: true, fields: [field('float', 1), field('int', 1), field('string', 'old')] }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const route = url.split('scene-editor/')[1];
    const body = options?.body ? JSON.parse(String(options.body)) : null;
    const reply = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value });
    if (route === 'version') return reply({ epoch: 'a', scene: 1, hierarchy, props, selection: 0, selectedId: null, compiling: false, playing: false });
    if (route === 'tree') return reply({ epoch: 'a', version: 1, nodes: [], scenes: [], total: 0, truncated: false });
    if (route.startsWith('inspect/')) return reply(data);
    if (route.startsWith('component-menu/')) { menuLoads++; return reply({ items: [{ item: 'Component/Thing', category: '', label: 'Thing', present: firstPresent && menuLoads === 1 }] }); }
    if (route === 'select') return reply({});
    posts.push({ route, body });
    if (held) await held;
    if (failure) return reply({ detail: failure }, 409);
    if (route === 'set-field') return reply({ componentId: body.componentId, field: { ...data.groups.flatMap(g => g.fields).find(f => f.path === body.field), value: body.value } });
    if (route === 'set-active') return reply({ id: body.id, activeSelf: body.active });
    return reply(body);
  }));
});
afterEach(() => { release?.(); release = undefined; cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const flush = async () => { await act(async () => {}); };
async function setup() { render(<Harness />); await flush(); act(() => editor.select(7)); await flush(); }
function edit(label: string, value: string) { const box = screen.getByRole('textbox', { name: label }); fireEvent.change(box, { target: { value } }); fireEvent.blur(box); return box; }
const poll = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); };
function hold() { held = new Promise<void>(resolve => { release = () => { held = null; resolve(); }; }); }

it('R1: truncated strings stay readonly and send nothing', async () => {
  data.groups[1].fields[2] = field('string', 'x'.repeat(2000), { readonly: true, truncated: true });
  await setup();
  expect(screen.getByText("Metin çok uzun; Unity'de düzenle")).toBeTruthy();
  const box = edit('string', 'replacement');
  expect(box).toHaveProperty('readOnly', true); await flush(); expect(posts).toEqual([]);
});
it('R2: unsafe integers revert without posting', async () => {
  await setup(); const box = edit('int', '9007199254740993'); await flush();
  expect(posts).toEqual([]); expect(box).toHaveProperty('value', '1');
});
it('R4: range drag commits once on release and remains enabled during its write', async () => {
  data.groups[1].fields[0].range = [0, 10]; await setup();
  const slider = screen.getByRole('slider', { name: 'float' }); hold();
  fireEvent.pointerDown(slider, { pointerId: 1, button: 0 });
  fireEvent.change(slider, { target: { value: '2' } }); fireEvent.change(slider, { target: { value: '3' } });
  await flush(); expect(posts).toEqual([]);
  fireEvent.pointerUp(slider, { pointerId: 1 }); await flush();
  expect(posts).toEqual([{ route: 'set-field', body: { componentId: 20, field: 'float', value: 3 } }]);
  expect(slider).toHaveProperty('disabled', false);
  fireEvent.pointerDown(slider, { pointerId: 2, button: 0 }); fireEvent.change(slider, { target: { value: '4' } });
  await act(async () => release!());
  expect(slider).toHaveProperty('value', '4');
  fireEvent.pointerUp(slider, { pointerId: 2 }); await flush(); expect(posts).toHaveLength(2);
  fireEvent.keyDown(slider, { key: 'ArrowRight' }); fireEvent.change(slider, { target: { value: '5' } }); fireEvent.keyUp(slider, { key: 'ArrowRight' });
  await flush(); expect(posts.at(-1)?.body.value).toBe(5);
});
it.each([false, true])('range capture loss commits once (pointerup first: %s)', async pointerUp => {
  data.groups[1].fields[0].range = [0, 10]; await setup();
  const slider = screen.getByRole('slider', { name: 'float' });
  fireEvent.pointerDown(slider, { pointerId: 1, button: 0 });
  fireEvent.change(slider, { target: { value: '3' } });
  await flush(); expect(posts).toEqual([]);
  if (pointerUp) fireEvent.pointerUp(slider, { pointerId: 1 });
  fireEvent.lostPointerCapture(slider, { pointerId: 1 }); await flush();
  expect(posts).toEqual([{ route: 'set-field', body: { componentId: 20, field: 'float', value: 3 } }]);
  fireEvent.change(slider, { target: { value: '4' } }); await flush();
  expect(posts).toHaveLength(2); expect(posts[1].body.value).toBe(4);
});
it('secondary pointerdown leaves range changes committing immediately', async () => {
  data.groups[1].fields[0].range = [0, 10]; await setup();
  const slider = screen.getByRole('slider', { name: 'float' });
  fireEvent.pointerDown(slider, { pointerId: 1, button: 2 });
  fireEvent.change(slider, { target: { value: '3' } }); await flush();
  expect(posts).toEqual([{ route: 'set-field', body: { componentId: 20, field: 'float', value: 3 } }]);
  fireEvent.pointerUp(slider, { pointerId: 1 }); fireEvent.lostPointerCapture(slider, { pointerId: 1 }); await flush();
  expect(posts).toHaveLength(1);
});
it.each(['float', 'name', 'active', 'enabled'])('R5: newer authoritative %s replaces the failed draft but preserves its error', async control => {
  await setup(); failure = 'write_failed';
  if (control === 'float') edit('float', '4');
  if (control === 'name') edit('Nesne adı', 'Rejected');
  if (control === 'active') fireEvent.click(screen.getByRole('checkbox', { name: 'Etkin' }));
  if (control === 'enabled') fireEvent.click(screen.getByRole('checkbox', { name: 'Behaviour' }));
  await flush(); expect(screen.getAllByRole('alert')).toHaveLength(1);
  // Same-value inspections must keep the rejected draft.
  props++; await poll();
  if (control === 'float') expect(screen.getByRole('textbox', { name: 'float' })).toHaveProperty('value', '4');
  if (control === 'name') expect(screen.getByRole('textbox', { name: 'Nesne adı' })).toHaveProperty('value', 'Rejected');
  data = { ...data, node: { ...data.node, name: 'Unity', activeSelf: false }, groups: data.groups.map(g => ({ ...g, enabled: false, fields: g.fields.map(f => f.kind === 'float' ? { ...f, value: 8 } : f) })) };
  props++; await poll();
  if (control === 'float') expect(screen.getByRole('textbox', { name: 'float' })).toHaveProperty('value', '8');
  if (control === 'name') expect(screen.getByRole('textbox', { name: 'Nesne adı' })).toHaveProperty('value', 'Unity');
  if (control === 'active' || control === 'enabled') {
    // Return again to the initial authoritative value: the rejected draft must not revive.
    data = { ...data, node: { ...data.node, activeSelf: true }, groups: data.groups.map(g => ({ ...g, enabled: true })) };
    props++; await poll();
    expect(screen.getByRole('checkbox', { name: control === 'active' ? 'Etkin' : 'Behaviour' })).toHaveProperty('checked', true);
  }
  expect(screen.getAllByRole('alert')).toHaveLength(1);
});
it('R6: hierarchy busy survives Inspector completions', async () => {
  await setup(); hold();
  act(() => { for (let i = 0; i < 8; i++) void editor.setField(20, 'float', i + 2); void editor.rename(7, 'Busy'); });
  await flush(); expect(editor.writeError?.code).toBe('busy');
  await act(async () => release!()); await flush(); expect(editor.writeError?.code).toBe('busy');
});
it.each([false, true])('R6: successful rename supersedes the same object error across channels (Inspector first: %s)', async inspector => {
  await setup(); failure = 'write_failed'; await act(async () => { await editor.rename(7, 'Rejected', inspector); });
  failure = null; await act(async () => { await editor.rename(8, 'Other', !inspector); });
  expect(inspector ? editor.inspectorWrites['name:7']?.error : editor.writeError).toBeTruthy();
  await act(async () => { await editor.rename(7, 'New', !inspector); });
  expect(inspector ? editor.inspectorWrites['name:7']?.error : editor.writeError).toBeFalsy();
  expect(screen.getByRole('textbox', { name: 'Nesne adı' })).toHaveProperty('value', 'New');
});
it('R5: any successful write to the same control retires a newer failed busy draft while retaining its error', async () => {
  await setup(); hold();
  act(() => { for (let i = 0; i < 8; i++) void editor.setField(20, 'float', 1); void editor.setField(20, 'float', 9); });
  await flush(); expect(screen.getByRole('textbox', { name: 'float' })).toHaveProperty('value', '9');
  await act(async () => release!()); await flush();
  expect(screen.getByRole('textbox', { name: 'float' })).toHaveProperty('value', '1');
  expect(editor.inspectorWrites['field:20:float']?.error?.code).toBe('busy');
});
it('R7: locked component toggle and menu cannot send requests', async () => {
  data.groups[1].locked = true; data.groups[1].removable = false;
  await setup(); const toggle = screen.getByRole('checkbox', { name: 'Behaviour' }); const menu = screen.getByRole('button', { name: 'Behaviour menüsü' });
  expect(toggle).toHaveProperty('disabled', true); expect(menu).toHaveProperty('disabled', true);
  fireEvent.click(toggle); fireEvent.click(menu); await flush(); expect(posts).toEqual([]); expect(screen.queryByRole('menu')).toBeNull();
});
it.each(['empty', 'readonly', 'truncated'])('%s fields do not lock the component toggle or menu', async mode => {
  if (mode === 'empty') data.groups[1].fields = [];
  else if (mode === 'truncated') data.groups[1].fields = [field('string', 'x'.repeat(2000), { readonly: true, truncated: true })];
  else data.groups[1].fields.forEach(f => { f.readonly = true; });
  await setup();
  const toggle = screen.getByRole('checkbox', { name: 'Behaviour' }); const menu = screen.getByRole('button', { name: 'Behaviour menüsü' });
  expect(toggle).toHaveProperty('disabled', false); expect(menu).toHaveProperty('disabled', false);
  fireEvent.click(toggle); await flush();
  expect(posts).toEqual([{ route: 'component-enable', body: { componentId: 20, enabled: false } }]);
  fireEvent.click(menu); expect(screen.getByRole('menu')).toBeTruthy();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sıfırla' })); await flush();
  expect(posts.at(-1)).toEqual({ route: 'component-action', body: { componentId: 20, action: 'reset' } });
});
it('readonly fields across every unlocked group leave component and object controls usable', async () => {
  data.groups.forEach(g => g.fields.forEach(f => { f.readonly = true; }));
  await setup();
  const toggle = screen.getByRole('checkbox', { name: 'Behaviour' }); const menu = screen.getByRole('button', { name: 'Behaviour menüsü' });
  const name = screen.getByRole('textbox', { name: 'Nesne adı' }); const active = screen.getByRole('checkbox', { name: 'Etkin' }); const add = screen.getByRole('button', { name: 'Bileşen ekle' });
  for (const control of [toggle, menu, active, add]) expect(control).toHaveProperty('disabled', false);
  expect(name).toHaveProperty('readOnly', false);
  expect(screen.getByRole('textbox', { name: 'float' })).toHaveProperty('readOnly', true);
  edit('float', '4'); await flush(); expect(posts).toEqual([]);
  fireEvent.click(toggle); await flush();
  expect(posts).toEqual([{ route: 'component-enable', body: { componentId: 20, enabled: false } }]);
  fireEvent.click(menu); expect(screen.getByRole('menu')).toBeTruthy();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sıfırla' })); await flush();
  expect(posts.at(-1)).toEqual({ route: 'component-action', body: { componentId: 20, action: 'reset' } });
  edit('Nesne adı', 'Renamed'); fireEvent.click(active); await flush();
  expect(posts.some(p => p.route === 'rename' && p.body.name === 'Renamed')).toBe(true);
  expect(posts.some(p => p.route === 'set-active' && p.body.active === false)).toBe(true);
  fireEvent.click(add); await flush(); expect(screen.getByRole('dialog')).toBeTruthy(); expect(menuLoads).toBe(1);
});
it('every group locked disables component and object controls without sending requests', async () => {
  data.groups.forEach(g => { g.locked = true; });
  await setup();
  const toggle = screen.getByRole('checkbox', { name: 'Behaviour' }); const menu = screen.getByRole('button', { name: 'Behaviour menüsü' });
  const name = screen.getByRole('textbox', { name: 'Nesne adı' }); const active = screen.getByRole('checkbox', { name: 'Etkin' }); const add = screen.getByRole('button', { name: 'Bileşen ekle' });
  for (const control of [toggle, menu, active, add]) expect(control).toHaveProperty('disabled', true);
  expect(name).toHaveProperty('readOnly', true);
  fireEvent.click(toggle); fireEvent.click(menu); fireEvent.click(active); fireEvent.click(add); edit('Nesne adı', 'Blocked'); await flush();
  expect(posts).toEqual([]); expect(menuLoads).toBe(0); expect(screen.queryByRole('menu')).toBeNull(); expect(screen.queryByRole('dialog')).toBeNull();
});
it.each([undefined, false])('locked Transform leaves an unlocked readonly component usable (locked: %s)', async locked => {
  data.groups[0].locked = true; data.groups[1].locked = locked;
  data.groups.forEach(g => g.fields.forEach(f => { f.readonly = true; }));
  await setup();
  expect(screen.getByRole('button', { name: 'Transform menüsü' })).toHaveProperty('disabled', true);
  const toggle = screen.getByRole('checkbox', { name: 'Behaviour' }); const menu = screen.getByRole('button', { name: 'Behaviour menüsü' });
  expect(toggle).toHaveProperty('disabled', false); expect(menu).toHaveProperty('disabled', false);
  fireEvent.click(toggle); await flush();
  expect(posts).toEqual([{ route: 'component-enable', body: { componentId: 20, enabled: false } }]);
  fireEvent.click(menu); expect(screen.getByRole('menu')).toBeTruthy();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sıfırla' })); await flush();
  expect(posts.at(-1)).toEqual({ route: 'component-action', body: { componentId: 20, action: 'reset' } });
});
it.each(['props', 'hierarchy'])('R8: %s changes discard cached present flags and refetch the open picker', async counter => {
  await setup(); fireEvent.click(screen.getByRole('button', { name: 'Bileşen ekle' })); await flush();
  expect(screen.getByRole('option', { name: /Thing/ }).getAttribute('aria-disabled')).toBe('true');
  if (counter === 'props') props++; else hierarchy++;
  await poll(); await flush();
  expect(menuLoads).toBe(2); expect(screen.getByRole('option', { name: /Thing/ }).getAttribute('aria-disabled')).toBeNull();
});
it('R8: invalid_item reloads the open picker instead of hanging on Loading', async () => {
  firstPresent = false; await setup();
  fireEvent.click(screen.getByRole('button', { name: 'Bileşen ekle' })); await flush(); failure = 'invalid_item';
  fireEvent.click(screen.getByRole('option', { name: /Thing/ })); await flush();
  expect(posts.some(p => p.route === 'add-component')).toBe(true);
  expect(within(screen.getByRole('dialog')).queryByText('Yükleniyor…')).toBeNull(); expect(menuLoads).toBe(2);
});
it('R10: rounds float and vector display without posting an untouched value', async () => {
  data.groups[1].fields[0].value = 0.050000000074505806; await setup();
  const scalar = screen.getByRole('textbox', { name: 'float' }); const x = screen.getByRole('textbox', { name: 'vec3 X' }); const y = screen.getByRole('textbox', { name: 'vec3 Y' });
  expect(scalar).toHaveProperty('value', '0.05'); expect(x).toHaveProperty('value', '-0.46342'); expect(y).toHaveProperty('value', '1e-7');
  expect(x.getAttribute('title')).toBe('-0.463420003');
  for (const box of [scalar, x, y]) { fireEvent.keyDown(box, { key: 'Enter' }); fireEvent.blur(box); }
  await flush(); expect(posts).toEqual([]);
  edit('float', '0.06'); await flush(); expect(posts.at(-1)?.body.value).toBe(0.06);
});
