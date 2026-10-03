import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { InspectorPane } from '../renderer/components/home/InspectorPane';
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor';
import { fetchComponentMenu, writeErrorCode } from '../renderer/lib/sceneEditor';
import type { ComponentMenuItem, Inspection } from '../renderer/lib/sceneEditor';

const items: ComponentMenuItem[] = [
  { item: 'Component/Physics/Rigidbody', category: 'Physics', label: 'Rigidbody', present: true },
  { item: 'Component/Physics/BoxCollider', category: 'Physics', label: 'Box Collider', present: false },
  { item: 'Component/Audio/AudioSource', category: 'Audio', label: 'Audio Source', present: false },
  { item: 'Component/Scripts/PlayerBrain', category: 'Scripts', label: 'Player/Brain', present: false },
];
let editor: ReturnType<typeof useSceneEditor>;
let menuCalls: number;
let menuStatus: number;
let menuItems: unknown;
let writes: { route: string; body: unknown }[];
let writeStatus: number;
let epoch: string;
let data: Inspection;
let token = 0;
function Harness() {
  editor = useSceneEditor({ api: 'api', token: `add-${token}`, editorOn: true, unityStatus: 'connected', hierarchyVisible: false, inspectorVisible: true });
  return <div className="ws-body"><div className="ws-pane"><InspectorPane inspection={editor.inspection} loading={editor.inspectLoading} error={editor.inspectError} stale={editor.stale} actions={editor} /></div></div>;
}
beforeEach(() => {
  token++; menuCalls = 0; menuStatus = 200; menuItems = items; writes = []; writeStatus = 200; epoch = 'a';
  data = { node: { id: 7, name: 'Cube', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: '' }, truncated: false,
    groups: [{ type: 'Transform', label: 'Transform', componentId: 10, enabled: null, removable: false, fields: [{ path: 'position', label: 'Position', kind: 'vec3', value: [0, 0, 0], readonly: false }] }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const route = url.split('scene-editor/')[1];
    const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
    if (route === 'version') return reply({ epoch, scene: 1, props: writes.length, selection: 0, selectedId: null, compiling: false, playing: false });
    if (route === 'tree') return reply({ epoch, version: 1, nodes: [], scenes: [], total: 0, truncated: false });
    if (route.startsWith('inspect/')) return reply(data);
    if (route === 'select') return reply({});
    if (route.startsWith('component-menu/')) { menuCalls++; return reply({ items: menuItems }, menuStatus); }
    const body = JSON.parse(String(options?.body)); writes.push({ route, body });
    if (writeStatus >= 400) return reply({ detail: 'add_failed' }, writeStatus);
    if (route === 'add-component') {
      data = { ...data, groups: [...data.groups, { type: 'BoxCollider', label: 'Box Collider', componentId: 99, enabled: true, removable: true, fields: [] }] };
      return reply({ componentId: 99, type: 'BoxCollider', label: 'Box Collider' });
    }
    return reply(body);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function setup(strict = false) {
  render(strict ? <React.StrictMode><Harness /></React.StrictMode> : <Harness />); await act(async () => {}); act(() => editor.select(7)); await screen.findByRole('button', { name: 'Bileşen ekle' });
}
async function open() {
  fireEvent.click(screen.getByRole('button', { name: 'Bileşen ekle' })); await screen.findByRole('option', { name: 'Physics' });
}
it('keeps Unity category order, drills in, goes back and searches label/category with result counts', async () => {
  await setup(); await open();
  expect(screen.getAllByRole('option').map(row => row.textContent)).toEqual(['Physics›', 'Audio›', 'Scripts›']);
  fireEvent.click(screen.getByRole('option', { name: 'Physics' }));
  expect(screen.getByRole('option', { name: /Rigidbody/ }).getAttribute('aria-disabled')).toBe('true');
  fireEvent.click(screen.getByRole('option', { name: /Rigidbody/ })); expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Geri' })); expect(screen.getByRole('option', { name: 'Audio' })).toBeTruthy();
  fireEvent.click(screen.getByRole('option', { name: 'Audio' }));
  const search = screen.getByRole('textbox', { name: 'Bileşen ara…' });
  fireEvent.keyDown(search, { key: 'ArrowLeft' }); expect(screen.getByRole('option', { name: 'Scripts' })).toBeTruthy();
  fireEvent.change(search, { target: { value: 'pHySiCs' } });
  expect(screen.getByText('2 sonuç')).toBeTruthy(); expect(screen.getAllByRole('option')).toHaveLength(2);
  fireEvent.change(search, { target: { value: 'bRaIn' } }); expect(screen.getByText('1 sonuç')).toBeTruthy(); expect(screen.getByText('Player/Brain')).toBeTruthy();
});
it('keyboard picks exactly once, closes, refreshes cache after add and finds the new component', async () => {
  await setup(); await open();
  const search = screen.getByRole('textbox', { name: 'Bileşen ara…' });
  fireEvent.keyDown(search, { key: 'Enter' }); fireEvent.keyDown(search, { key: 'ArrowDown' }); fireEvent.keyDown(search, { key: 'Enter' });
  await waitFor(() => expect(writes).toEqual([{ route: 'add-component', body: { id: 7, item: 'Component/Physics/BoxCollider' } }]));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await screen.findByRole('button', { name: 'Box Collider' });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Bileşen ekle' }));
  await open(); expect(menuCalls).toBe(2);
});
it('Escape refocuses the opener and reopening uses the object cache', async () => {
  await setup(); await open();
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'Bileşen ara…' }), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull(); expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Bileşen ekle' }));
  await open(); expect(menuCalls).toBe(1);
});
it.each([['HTTP failure', 503, items], ['malformed list', 200, {}], ['empty list', 200, []], ['malformed entries', 200, [{ ...items[0], present: 'yes' }]]])('fetch %s shows retry and is not cached', async (_, status, entries) => {
  await setup(); menuStatus = status as number; menuItems = entries;
  fireEvent.click(screen.getByRole('button', { name: 'Bileşen ekle' }));
  const alert = await screen.findByRole('alert'); expect(alert.textContent).toContain('Bileşen listesi alınamadı');
  menuStatus = 200; menuItems = items;
  fireEvent.click(within(alert).getByRole('button', { name: 'Tekrar dene' }));
  await screen.findByRole('option', { name: 'Physics' }); expect(menuCalls).toBe(2);
});
it('validates menu entries and new conflict codes', async () => {
  menuItems = [null, { item: 'bad', category: '', label: 'Bad' }, { ...items[0], item: 4 }, items[1]];
  expect(await fetchComponentMenu('api', 't', 7)).toEqual([items[1]]);
  for (const code of ['already_present', 'required', 'add_failed']) expect(writeErrorCode(409, code)).toBe(code);
});
it('attributes add failure to the picker and closes after a successful retry', async () => {
  await setup(); await open(); writeStatus = 409;
  fireEvent.click(screen.getByRole('option', { name: 'Physics' })); fireEvent.click(screen.getByRole('option', { name: /Box Collider/ }));
  const alert = await screen.findByRole('alert'); expect(alert.textContent).toContain('Unity bu bileşeni ekleyemedi.'); expect(editor.writeError).toBeNull();
  writeStatus = 200; fireEvent.click(within(alert).getByRole('button', { name: 'Tekrar dene' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull()); expect(writes).toHaveLength(2);
});
it('drops component cache after remove and a Unity epoch change', async () => {
  await setup(); await open(); fireEvent.keyDown(screen.getByRole('textbox', { name: 'Bileşen ara…' }), { key: 'Escape' });
  await act(async () => { await editor.componentAction(99, 'remove'); });
  await open(); expect(menuCalls).toBe(2); fireEvent.keyDown(screen.getByRole('textbox', { name: 'Bileşen ara…' }), { key: 'Escape' });
  epoch = 'b';
  await waitFor(() => expect(editor.componentMenu.state).toBe('idle'), { timeout: 2500 });
  await open(); expect(menuCalls).toBe(3);
});
it('does not retry an add or component action whose reply may have been lost', async () => {
  await setup(); writeStatus = 504;
  await act(async () => { await editor.addComponent(7, items[1].item); await editor.componentAction(10, 'reset'); });
  const errors = Object.values(editor.inspectorWrites).map(write => write.error);
  expect(errors).toHaveLength(2); expect(errors.every(error => error?.unsure && !error.retry)).toBe(true); expect(editor.writeError).toBeNull();
});
it('closes after adding under StrictMode effect remounts', async () => {
  await setup(true); await open();
  fireEvent.click(screen.getByRole('option', { name: 'Audio' })); fireEvent.click(screen.getByRole('option', { name: /Audio Source/ }));
  await waitFor(() => expect(writes).toHaveLength(1)); await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});
it('scrolls the actual workspace pane to the component after the inspection refresh', async () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('ws-pane')) return new DOMRect(0, 0, 300, 400);
    if (this.dataset.componentId === '99') return new DOMRect(0, 500, 300, 100);
    return new DOMRect(0, 0, 0, 0);
  });
  await setup(); await open();
  fireEvent.click(screen.getByRole('option', { name: 'Physics' })); fireEvent.click(screen.getByRole('option', { name: /Box Collider/ }));
  await screen.findByRole('button', { name: 'Box Collider' });
  expect(document.querySelector('.ws-pane')!.scrollTop).toBe(200); expect(document.querySelector('.ws-body')!.scrollTop).toBe(0);
});
