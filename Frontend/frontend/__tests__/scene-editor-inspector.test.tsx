import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { InspectorPane } from '../renderer/components/home/InspectorPane'
import { ChangedFiles, ScenePane } from '../renderer/components/home/Workspace'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import type { Inspection, SceneField } from '../renderer/lib/sceneEditor'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })
const field = (kind: SceneField['kind'], value: unknown, extra = {}) => ({ path: kind, label: kind, kind, value, readonly: true, ...extra })
const inspection: Inspection = {
  node: { id: -2, name: 'Cube', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'root', globalId: 'g' },
  groups: [{ type: 'Transform', label: 'Transform', componentId: 1, enabled: null, removable: false, fields: [
    field('float', 1.5, { range: { min: 0, max: 10 } }), field('int', 2), field('bool', true), field('enum', 1, { options: ['A', 'B'] }), field('string', 'text'),
    field('vec2', { x: 1, y: 2 }), field('vec3', [1, 2, 3]), field('vec4', { x: 1, y: 2, z: 3, w: 4 }),
    field('color', '#F2C230FF'), field('ref', { name: 'Ball', type: 'Transform', id: 3 }), field('ref', null, { path: 'nullRef', label: 'nullRef' }),
    field('mask', 5, { options: ['Default', 'TransparentFX', 'Ignore Raycast'] }), field('list', 4), field('unsupported', 'Matrix4x4'),
  ] }, { type: 'missing', label: 'missing', componentId: 2, enabled: null, removable: false, fields: [] }], truncated: false,
}

it('shows an empty state and all field kinds read-only with collapsible groups', () => {
  const view = render(<InspectorPane inspection={null} loading={false} stale={false} error={null} />)
  expect(screen.getByText('Hiyerarşiden bir nesne seç.')).toBeTruthy()
  view.rerender(<InspectorPane inspection={inspection} loading={false} stale error={null} />)
  expect(screen.getByText('eski')).toBeTruthy()
  expect(screen.getByText('B')).toBeTruthy()
  expect(screen.getByText('Ball (Transform)')).toBeTruthy()
  expect(screen.getByText('4 öğe')).toBeTruthy()
  expect(screen.getByText('Matrix4x4').className).toContain('f-unsupported')
  expect(screen.getByText('#F2C230FF')).toBeTruthy()
  expect(screen.getByText('Default, Ignore Raycast')).toBeTruthy()
  expect(screen.getByText('Eksik script')).toBeTruthy()
  const inputs = Array.from(view.container.querySelectorAll('input'))
  expect(inputs.length).toBeGreaterThan(12)
  expect(inputs.every(input => input.disabled || input.readOnly)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Transform' }))
  expect(view.container.querySelector('.cmp-body')?.getAttribute('hidden')).not.toBeNull()
})

it('keeps last data during compilation and clears a deleted selection on refresh', async () => {
  vi.useFakeTimers()
  let scene = 1, compiling = false, deleted = false
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: !(deleted && url.includes('inspect')), status: deleted && url.includes('inspect') ? 404 : 200,
    json: async () => url.includes('inspect') ? inspection : url.endsWith('tree') ? { epoch: 'a', version: scene, scenes: [], nodes: [], total: 0, truncated: false }
      : { epoch: 'a', scene, selection: 0, selectedId: null, compiling, playing: false } })))
  const h = renderHook(() => useSceneEditor({ api: 'api', token: 't', editorOn: true, unityStatus: 'connected', hierarchyVisible: false, inspectorVisible: true }))
  await act(async () => {})
  act(() => h.result.current.select(-2))
  await act(async () => {})
  expect(h.result.current.inspection?.node.name).toBe('Cube')
  compiling = true; scene = 2
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(h.result.current.stale).toBe(true)
  expect(h.result.current.inspection?.node.name).toBe('Cube')
  compiling = false; deleted = true
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(h.result.current.inspection).toBeNull()
  expect(h.result.current.selectedId).toBeNull()
})

it('collapses large component sets except Transform and translates shell field labels', () => {
  const many = { ...inspection, groups: [inspection.groups[0], ...Array.from({ length: 6 }, (_, index) => ({
    type: 'Behaviour', label: `Component ${index}`, componentId: index + 10, enabled: false, removable: false, fields: [field('string', 'private value')],
  }))] }
  const view = render(<LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (key, vars) => ceviriUygula('en', key, vars) }}>
    <InspectorPane inspection={many} loading={false} error={null} stale={false} />
  </LangContext.Provider>)
  expect(screen.getByRole('button', { name: 'Transform' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('button', { name: 'Component 0' }).getAttribute('aria-expanded')).toBe('false')
  expect(screen.getByText('4 items')).toBeTruthy()
  expect(screen.getByRole('checkbox', { name: 'Component 0' }).getAttribute('disabled')).not.toBeNull()
  expect(view.container.querySelectorAll('.cmp-body[hidden]')).toHaveLength(6)
})

it('loads a selection deferred during compilation when compilation finishes without a scene change', async () => {
  vi.useFakeTimers()
  let compiling = true
  const fetcher = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.includes('inspect') ? inspection
    : url.endsWith('tree') ? { epoch: 'a', version: 1, scenes: [], nodes: [], total: 0, truncated: false }
      : { epoch: 'a', scene: 1, selection: 0, selectedId: null, compiling, playing: false } }))
  vi.stubGlobal('fetch', fetcher)
  const h = renderHook(() => useSceneEditor({ api: 'api', token: 't', editorOn: true, unityStatus: 'connected', hierarchyVisible: false, inspectorVisible: true }))
  await act(async () => {})
  act(() => h.result.current.select(-2))
  await act(async () => {})
  expect(h.result.current.stale).toBe(true)
  expect(fetcher.mock.calls.some(([url]) => url.includes('inspect'))).toBe(false)
  compiling = false
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(h.result.current.inspection?.node.id).toBe(-2)
  expect(h.result.current.stale).toBe(false)
})

it('reuses the changed-files block with the same acknowledge action and wires it into Files', () => {
  const ack = vi.fn()
  const props = { change: null, changed: [{ path: 'A.cs', rel: 'A.cs', status: 'modified' }], changedTotal: 1, isRepo: true,
    workspacePath: null, onAck: ack, onShowChange: vi.fn(), onOpen: vi.fn() }
  const view = render(<ChangedFiles {...props} />)
  fireEvent.click(screen.getByText('Gördüm', { selector: 'button' }))
  expect(ack).toHaveBeenCalledOnce()
  view.rerender(<InspectorPane inspection={inspection} loading={false} error={null} stale={false} />)
  expect(view.container.querySelector('[data-guide="changed-files"]')).toBeNull()
  view.rerender(<ScenePane {...props} />)
  expect(view.container.querySelector('[data-guide="changed-files"]')).toBeTruthy()
  const source = ts.createSourceFile('home.tsx', readFileSync('renderer/pages/home.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let panes = ''
  const visit = (n: ts.Node) => {
    if (ts.isJsxAttribute(n) && n.name.getText(source) === 'panes') panes = n.initializer?.getText(source) ?? ''
    ts.forEachChild(n, visit)
  }
  visit(source)
  expect(panes).toMatch(/sahne:\s*editorOn\s*\?\s*<InspectorPane/)
  expect(panes).toMatch(/dosyalar:[\s\S]*editorOn && <ChangedFiles/)
})
