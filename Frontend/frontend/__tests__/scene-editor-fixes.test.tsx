import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { HierarchyPanel } from '../renderer/components/home/HierarchyPanel'
import { InspectorPane } from '../renderer/components/home/InspectorPane'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import { guideWorkspaceTab } from '../renderer/lib/guide/prepare'
import type { Inspection, SceneTree, SceneVersion } from '../renderer/lib/sceneEditor'

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

const node = (id: number, name: string, parentId: number | null, childCount = 0) => ({
  id, name, parentId, childCount, index: 0, activeSelf: true, activeInHierarchy: true, prefab: 'none' as const, scene: 'Scene',
})
const treeOf = (version: number, name = `Root v${version}`): SceneTree => ({
  epoch: 'a', version, scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: [1] }],
  nodes: [node(1, name, null)], total: 1, truncated: false,
})
const inspection = (name: string): Inspection => ({
  node: { id: 1, name, activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: 'g' },
  groups: [], truncated: false,
})
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body }) as Response

type Server = { version: SceneVersion; tree: () => Response; inspect: () => Response }
function serve(server: Server) {
  const fetcher = vi.fn(async (url: string, _options?: RequestInit) => url.endsWith('tree') ? server.tree()
    : url.includes('inspect') ? server.inspect() : url.endsWith('select') ? reply(200, {}) : reply(200, server.version))
  vi.stubGlobal('fetch', fetcher)
  const count = (part: string) => fetcher.mock.calls.filter(([url]) => url.includes(part)).length
  return { fetcher, count }
}
const version = (extra: Partial<SceneVersion> = {}): SceneVersion =>
  ({ epoch: 'a', scene: 1, selection: 0, selectedId: null, compiling: false, playing: false, ...extra })
const opts = { api: 'api', token: 't', editorOn: true, unityStatus: 'connected' as const, hierarchyVisible: true, inspectorVisible: true }
const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

it('F1: a tree refresh that fails once is retried until it succeeds', async () => {
  vi.useFakeTimers()
  let treeVersion = 1, failNext = false
  const server: Server = { version: version(), inspect: () => reply(200, inspection('Cube')),
    tree: () => { if (failNext) { failNext = false; return reply(503, {}) } return reply(200, treeOf(treeVersion)) } }
  serve(server)
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  expect(h.result.current.tree?.version).toBe(1)
  server.version = version({ scene: 2 }); treeVersion = 2; failNext = true
  await tick()
  expect(h.result.current.tree?.version).toBe(1)
  expect(h.result.current.error).toBe(503)
  await tick(); await tick()
  expect(h.result.current.tree?.version).toBe(2)
  expect(h.result.current.error).toBeNull()
})

it('F1: an inspector refresh that fails once is retried until it succeeds', async () => {
  vi.useFakeTimers()
  let name = 'Cube', failNext = false
  const server: Server = { version: version(), tree: () => reply(200, treeOf(1)),
    inspect: () => { if (failNext) { failNext = false; return reply(503, {}) } return reply(200, inspection(name)) } }
  serve(server)
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  act(() => h.result.current.select(1))
  await act(async () => {})
  expect(h.result.current.inspection?.node.name).toBe('Cube')
  server.version = version({ scene: 2 }); name = 'Sphere'; failNext = true
  await tick()
  expect(h.result.current.inspectError).toBe(503)
  await tick(); await tick()
  expect(h.result.current.inspection?.node.name).toBe('Sphere')
  expect(h.result.current.inspectError).toBeNull()
})

it('F2: a failing tree endpoint backs off exponentially and resets on success', async () => {
  vi.useFakeTimers()
  let down = true
  const server: Server = { version: version(), inspect: () => reply(200, inspection('Cube')),
    tree: () => down ? reply(503, {}) : reply(200, treeOf(server.version.scene)) }
  const { count } = serve(server)
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  await tick(15000)
  // Attempts at 0, 1, 3 and 7 s; the next one waits until 15 s.
  expect(count('tree')).toBeLessThanOrEqual(5)
  expect(count('tree')).toBeGreaterThanOrEqual(4)
  const failing = count('tree')
  await tick(10000)
  // The delay is capped at 30 s, so at most one more attempt fits in 10 s.
  expect(count('tree') - failing).toBeLessThanOrEqual(1)
  down = false
  await tick(31000)
  expect(h.result.current.tree?.version).toBe(1)
  const recovered = count('tree')
  server.version = version({ scene: 2 })
  await tick()
  expect(count('tree')).toBe(recovered + 1)
  expect(h.result.current.tree?.version).toBe(2)
})

it('F2: inspector backoff resets on a new selection and on an epoch change', async () => {
  vi.useFakeTimers()
  const server: Server = { version: version(), tree: () => reply(200, treeOf(1)), inspect: () => reply(504, {}) }
  const { count } = serve(server)
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  act(() => h.result.current.select(1))
  await act(async () => {})
  await tick(10000)
  expect(count('inspect')).toBeLessThanOrEqual(5)
  const before = count('inspect')
  act(() => h.result.current.select(1))
  await act(async () => {})
  expect(count('inspect')).toBe(before + 1)
  await tick(1000)
  expect(count('inspect')).toBe(before + 2)
  await tick(10000)
  const settled = count('inspect')
  server.version = version({ epoch: 'b' })
  await tick(1000)
  expect(count('inspect')).toBe(settled + 1)
})

it('F3: the tree follows the hierarchy counter and the inspector follows props or hierarchy', async () => {
  vi.useFakeTimers()
  const server: Server = { version: version({ hierarchy: 1, props: 1 }), tree: () => reply(200, treeOf(1)), inspect: () => reply(200, inspection('Cube')) }
  const { count } = serve(server)
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  act(() => h.result.current.select(1))
  await act(async () => {})
  await tick()
  expect([count('tree'), count('inspect')]).toEqual([1, 1])
  server.version = version({ scene: 2, hierarchy: 1, props: 2 })
  await tick()
  expect([count('tree'), count('inspect')]).toEqual([1, 2])
  server.version = version({ scene: 3, hierarchy: 2, props: 2 })
  await tick()
  expect([count('tree'), count('inspect')]).toEqual([2, 3])
  server.version = version({ scene: 4, hierarchy: 2, props: 2 })
  await tick()
  expect([count('tree'), count('inspect')]).toEqual([2, 3])
  server.version = version({ epoch: 'b', scene: 4, hierarchy: 2, props: 2 })
  await tick()
  expect(count('tree')).toBe(3)
  h.unmount()
})

it('F4: the changed-files step opens Files when the scene editor is on, Scene when off', () => {
  expect(guideWorkspaceTab('scene', false)).toBe('scene')
  expect(guideWorkspaceTab('scene', true)).toBe('files')
  expect(guideWorkspaceTab('code', true)).toBe('code')
  const home = readFileSync('renderer/pages/home.tsx', 'utf8')
  expect(home).toMatch(/'workspace\.tab':\s*\(tab\)\s*=>\s*\{[^}]*GUIDE_TABS\[guideWorkspaceTab\(tab,\s*editorOn\)\]/)
})

const deep: SceneTree = {
  epoch: 'a', version: 1, scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: [1] }],
  nodes: [node(1, 'Root', null, 1), node(2, 'Child', 1, 1), node(3, 'Leaf', 2)], total: 3, truncated: false,
}
const panel = { unityStatus: 'connected' as const, tree: deep, loading: false, error: null, stale: false, onConnect: () => {} }

it('F5: keyboard works from the first visible row when the selection is hidden', () => {
  const select = vi.fn()
  render(<HierarchyPanel {...panel} selectedId={3} onSelect={select} />)
  // A filter hides the selected leaf.
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'root' } })
  expect(screen.queryByRole('treeitem', { name: 'Leaf' })).toBeNull()
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'Home' })
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'Enter' })
  expect(select).toHaveBeenLastCalledWith(1)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } })
  // A collapsed ancestor hides it.
  fireEvent.click(screen.getByRole('button', { name: 'Child' }))
  expect(screen.queryByRole('treeitem', { name: 'Leaf' })).toBeNull()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'x' } })
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } })
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'End' })
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'Enter' })
  expect(select).toHaveBeenLastCalledWith(2)
})

it('F5: a selection from outside the tree expands its ancestors once', () => {
  const view = render(<HierarchyPanel {...panel} selectedId={null} onSelect={() => {}} />)
  expect(screen.queryByRole('treeitem', { name: 'Leaf' })).toBeNull()
  view.rerender(<HierarchyPanel {...panel} selectedId={3} onSelect={() => {}} />)
  expect(screen.getByRole('treeitem', { name: 'Leaf' }).getAttribute('aria-selected')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Child' }))
  view.rerender(<HierarchyPanel {...panel} tree={{ ...deep }} selectedId={3} onSelect={() => {}} />)
  expect(screen.queryByRole('treeitem', { name: 'Leaf' })).toBeNull()
})

const groupOf = (type: string, componentId: number | null) =>
  ({ type, label: type.split('.').pop()!, componentId, enabled: null, removable: false, fields: [] })

it('F6: Transform and RectTransform start expanded under their full Unity names', () => {
  const few = { ...inspection('Cube'), groups: [groupOf('UnityEngine.Transform', 1)] }
  const view = render(<InspectorPane inspection={few} loading={false} error={null} stale={false} />)
  expect(screen.getByRole('button', { name: 'Transform' }).getAttribute('aria-expanded')).toBe('true')
  const many = { ...inspection('Cube'), groups: [groupOf('UnityEngine.RectTransform', 1), groupOf('UnityEngine.Transform', 2),
    ...Array.from({ length: 6 }, (_, i) => groupOf(`Behaviour${i}`, i + 10))] }
  view.rerender(<InspectorPane inspection={many} loading={false} error={null} stale={false} />)
  expect(screen.getByRole('button', { name: 'RectTransform' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('button', { name: 'Transform' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('button', { name: 'Behaviour0' }).getAttribute('aria-expanded')).toBe('false')
})

it('F7: several missing scripts keep unique keys and non-numeric floats render as text', () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
  const data = { ...inspection('Cube'), groups: [groupOf('missing', null), groupOf('missing', null), {
    ...groupOf('Light', 3), fields: [
      { path: 'a', label: 'a', kind: 'float' as const, value: 'NaN', readonly: true, range: { min: 0, max: 1 } },
      { path: 'b', label: 'b', kind: 'float' as const, value: 'Infinity', readonly: true },
    ] }] }
  const view = render(<InspectorPane inspection={data} loading={false} error={null} stale={false} />)
  expect(screen.getAllByText('Eksik script')).toHaveLength(2)
  expect(screen.getByDisplayValue('NaN')).toBeTruthy()
  expect(screen.getByDisplayValue('Infinity')).toBeTruthy()
  expect(view.container.querySelector('input[type="range"]')).toBeNull()
  expect(errors).not.toHaveBeenCalled()
})
