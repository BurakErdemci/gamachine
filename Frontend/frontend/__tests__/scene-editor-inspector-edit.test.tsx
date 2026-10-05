import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { InspectorPane } from '../renderer/components/home/InspectorPane';
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor';
import type { Inspection, SceneField } from '../renderer/lib/sceneEditor';

const f = (kind: SceneField['kind'], value: unknown, extra = {}): SceneField => ({ path: kind, label: kind, kind, value, readonly: false, ...extra });
let data: Inspection;
let editor: ReturnType<typeof useSceneEditor>;
let calls: { route: string; body: any }[];
let failure: string | null;
let stale: boolean;
let held: Promise<void> | null;
let release: (() => void) | undefined;
let token = 0;
function Harness() {
  editor = useSceneEditor({ api: 'api', token: `edit-${token}`, editorOn: true, unityStatus: 'connected', hierarchyVisible: true, inspectorVisible: true });
  return <InspectorPane inspection={editor.inspection} loading={editor.inspectLoading} error={editor.inspectError} stale={stale} actions={editor} />;
}
beforeEach(() => {
  token++; calls = []; failure = null; stale = false; held = null;
  data = { node: { id: 7, name: 'Cube', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: '' }, truncated: false,
    groups: [{ type: 'Transform', label: 'Transform', componentId: 10, enabled: null, removable: false, fields: [f('vec3', [1, 2, 3])] },
      { type: 'Behaviour', label: 'Behaviour', componentId: 20, enabled: true, removable: true, fields: [f('float', 1), f('int', 1), f('string', 'old'), f('bool', false), f('enum', 0, { options: ['A', 'B', 'C'] }), f('vec2', [1, 2]), f('vec4', [1, 2, 3, 4]), f('color', '#11223344'), f('mask', 0, { options: ['Default', '', 'Water'] })] },
      { type: 'Last', label: 'Last', componentId: 30, enabled: true, removable: false, fields: [] }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const route = url.split('scene-editor/')[1];
    const body = options?.body ? JSON.parse(String(options.body)) : null;
    const reply = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value });
    if (route === 'version') return reply({ epoch: 'a', scene: 1, selection: 0, selectedId: null, compiling: false, playing: false });
    if (route === 'tree') return reply({ epoch: 'a', version: 1, nodes: [], scenes: [], total: 0, truncated: false });
    if (route.startsWith('inspect/')) return reply(data);
    if (route === 'select') return reply({});
    calls.push({ route, body });
    if (held) await held;
    if (failure) return reply({ detail: failure }, 409);
    if (route === 'set-active') return reply({ id: body.id, activeSelf: body.active, activeInHierarchy: body.active });
    if (route === 'set-field') {
      const field = data.groups.flatMap(g => g.fields).find(field => field.path === body.field)!;
      return reply({ componentId: body.componentId, field: { ...field, value: body.value } });
    }
    return reply(body);
  }));
});
afterEach(() => { release?.(); release = undefined; cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function setup() {
  const view = render(<Harness />);
  await act(async () => {});
  act(() => editor.select(7));
  await screen.findByRole('textbox', { name: 'float' });
  return view;
}
const posts = (route: string) => calls.filter(c => c.route === route).map(c => c.body);
it.each([
  ['float', '2.5', 2.5], ['int', '2.8', 3], ['string', 'new', 'new'],
  ['vec2 X', '9', [9, 2]], ['vec3 Y', '9', [1, 9, 3]], ['vec4 W', '9', [1, 2, 3, 9]],
])('commits %s once with its encoded value, even after blur', async (label, input, value) => {
  await setup();
  const box = screen.getByRole('textbox', { name: label });
  fireEvent.change(box, { target: { value: input } }); fireEvent.keyDown(box, { key: 'Enter' }); fireEvent.blur(box);
  await waitFor(() => expect(posts('set-field')).toHaveLength(1));
  expect(posts('set-field')).toEqual([{ componentId: label.startsWith('vec3') ? 10 : 20, field: label.split(' ')[0], value }]);
});
it('commits bool, enum, color and mask with the correct bodies', async () => {
  await setup();
  fireEvent.click(screen.getByRole('checkbox', { name: 'bool' }));
  fireEvent.click(screen.getByRole('button', { name: 'enum' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: 'B' }));
  fireEvent.click(screen.getByRole('button', { name: 'color' }));
  const color = screen.getByRole('textbox', { name: 'color' });
  fireEvent.change(color, { target: { value: '#ABCDEF80' } }); fireEvent.blur(color);
  fireEvent.click(screen.getByRole('button', { name: 'mask' }));
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Water' }));
  await waitFor(() => expect(posts('set-field')).toHaveLength(4));
  expect(posts('set-field')).toEqual([
    { componentId: 20, field: 'bool', value: true }, { componentId: 20, field: 'enum', value: 1 },
    { componentId: 20, field: 'color', value: '#ABCDEF80' }, { componentId: 20, field: 'mask', value: 4 },
  ]);
});
it('invalid numbers/colors and Escape revert without writes; blur commits strings', async () => {
  await setup();
  const box = screen.getByRole('textbox', { name: 'float' }) as HTMLInputElement;
  for (const value of ['oops', '', 'Infinity', '12junk']) { fireEvent.change(box, { target: { value } }); fireEvent.blur(box); expect(box.value).toBe('1'); }
  fireEvent.change(box, { target: { value: '4' } }); fireEvent.keyDown(box, { key: 'Escape' }); fireEvent.blur(box);
  fireEvent.click(screen.getByRole('button', { name: 'color' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'color' }), { target: { value: '#wrong' } }); fireEvent.blur(screen.getByRole('textbox', { name: 'color' }));
  expect(posts('set-field')).toEqual([]);
  const string = screen.getByRole('textbox', { name: 'string' });
  fireEvent.change(string, { target: { value: 'blur' } }); fireEvent.blur(string);
  await waitFor(() => expect(posts('set-field')).toEqual([{ componentId: 20, field: 'string', value: 'blur' }]));
});
it.each(['readonly', 'stale', 'locked'])('%s controls send no writes', async mode => {
  if (mode === 'readonly') data.groups[1].fields[0].readonly = true;
  if (mode === 'locked') data.groups.forEach(g => { g.locked = true; g.fields.forEach(f => { f.readonly = true; }); });
  if (mode === 'stale') stale = true;
  await setup();
  const box = screen.getByRole('textbox', { name: 'float' });
  fireEvent.change(box, { target: { value: '9' } }); fireEvent.keyDown(box, { key: 'Enter' }); fireEvent.blur(box);
  if (mode !== 'readonly') {
    fireEvent.click(screen.getByRole('checkbox', { name: 'Etkin' }));
    fireEvent.click(screen.getByRole('button', { name: 'Bileşen ekle' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  }
  await act(async () => {}); expect(calls).toEqual([]);
});
it('keeps the pending value and returned field, with errors isolated to their row and Retry/Revert', async () => {
  await setup();
  held = new Promise<void>(r => { release = () => { held = null; r(); }; });
  const box = screen.getByRole('textbox', { name: 'float' }) as HTMLInputElement;
  fireEvent.change(box, { target: { value: '4' } }); fireEvent.blur(box);
  await waitFor(() => expect(posts('set-field')).toHaveLength(1));
  expect(box.value).toBe('4'); expect(box.closest('.fr')?.className).toContain('is-writing');
  failure = 'write_failed'; await act(async () => release!());
  const error = await screen.findByRole('alert');
  expect(error.className).toContain('fr-err'); expect(editor.writeError).toBeNull();
  expect(within(error).getByRole('button', { name: 'Tekrar dene' })).toBeTruthy();
  fireEvent.click(within(error).getByRole('button', { name: 'Geri al' }));
  expect(screen.queryByRole('alert')).toBeNull(); expect(box.value).toBe('1');
  fireEvent.change(box, { target: { value: '5' } }); fireEvent.blur(box);
  await screen.findByRole('alert'); failure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Tekrar dene' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull()); expect(box.value).toBe('5');
});
it('writes active, name and enabled; empty name and Escape send nothing', async () => {
  await setup();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Etkin' }));
  const name = screen.getByRole('textbox', { name: 'Nesne adı' });
  fireEvent.change(name, { target: { value: '' } }); fireEvent.blur(name);
  fireEvent.change(name, { target: { value: 'Ignored' } }); fireEvent.keyDown(name, { key: 'Escape' }); fireEvent.blur(name);
  fireEvent.change(name, { target: { value: 'Ball' } }); fireEvent.keyDown(name, { key: 'Enter' }); fireEvent.blur(name);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Behaviour' }));
  await waitFor(() => expect(calls).toHaveLength(3));
  expect(posts('set-active')).toEqual([{ id: 7, active: false }]); expect(posts('rename')).toEqual([{ id: 7, name: 'Ball' }]);
  expect(posts('component-enable')).toEqual([{ componentId: 20, enabled: false }]);
});
it('enum arrows/Enter select an index and Escape returns focus', async () => {
  await setup(); const button = screen.getByRole('button', { name: 'enum' });
  fireEvent.click(button); fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' }); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Enter' });
  await waitFor(() => expect(posts('set-field')).toEqual([{ componentId: 20, field: 'enum', value: 1 }]));
  fireEvent.click(button); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' }); expect(document.activeElement).toBe(button);
});
it('component menu guards disabled actions and supports arrows, Home/End, Enter, Escape and Tab', async () => {
  await setup();
  const button = screen.getByRole('button', { name: 'Transform menüsü' }); fireEvent.click(button);
  const menu = screen.getByRole('menu');
  for (const name of ['Bileşeni kaldır', 'Yukarı taşı', 'Aşağı taşı']) { const item = within(menu).getByRole('menuitem', { name }); expect(item.getAttribute('aria-disabled')).toBe('true'); fireEvent.click(item); }
  expect(posts('component-action')).toEqual([]); fireEvent.keyDown(menu, { key: 'Escape' }); expect(document.activeElement).toBe(button);
  fireEvent.click(screen.getByRole('button', { name: 'Behaviour menüsü' }));
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' }); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Enter' });
  await waitFor(() => expect(posts('component-action')).toEqual([{ componentId: 20, action: 'down' }]));
  fireEvent.click(screen.getByRole('button', { name: 'Last menüsü' }));
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' }); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Home' }); fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' }); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Enter' });
  await waitFor(() => expect(posts('component-action')).toContainEqual({ componentId: 30, action: 'up' }));
  fireEvent.click(screen.getByRole('button', { name: 'Behaviour menüsü' })); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' }); expect(screen.queryByRole('menu')).toBeNull();
});
it.each([['reset', 'Sıfırla'], ['remove', 'Bileşeni kaldır']])('component %s sends exactly one action', async (action, name) => {
  await setup(); fireEvent.click(screen.getByRole('button', { name: 'Behaviour menüsü' })); fireEvent.click(screen.getByRole('menuitem', { name }));
  await waitFor(() => expect(posts('component-action')).toEqual([{ componentId: 20, action }]));
});
it('scrubs numeric labels once on release and clamps range inputs', async () => {
  data.groups[1].fields[0].range = { min: 0, max: 2 };
  await setup();
  fireEvent.pointerDown(screen.getByText('int', { selector: '.fr-l' }), { clientX: 10, pointerId: 1 });
  fireEvent.pointerMove(document, { clientX: 20, pointerId: 1 }); fireEvent.pointerUp(document, { clientX: 20, pointerId: 1 });
  await waitFor(() => expect(posts('set-field')).toEqual([{ componentId: 20, field: 'int', value: 6 }]));
  const box = screen.getByRole('textbox', { name: 'float' }); fireEvent.change(box, { target: { value: '20' } }); fireEvent.blur(box);
  await waitFor(() => expect(posts('set-field')).toContainEqual({ componentId: 20, field: 'float', value: 2 }));
});
it('shows the activeSelf Unity replies after turning an inactive object on', async () => {
  data.node.activeSelf = false;
  await setup();
  const box = screen.getByRole('checkbox', { name: 'Etkin' });
  fireEvent.click(box);
  await waitFor(() => expect(posts('set-active')).toEqual([{ id: 7, active: true }]));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Etkin' })).toHaveProperty('checked', true));
});
