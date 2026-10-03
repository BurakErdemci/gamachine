import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { HierarchyPanel } from '../renderer/components/home/HierarchyPanel'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { Workspace } from '../renderer/components/home/Workspace'
import { InspectorPane } from '../renderer/components/home/InspectorPane'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import { writeSceneEditor, type SceneTree } from '../renderer/lib/sceneEditor'

const noop = () => {}
const node = (id: number, name: string, parentId: number | null, childCount = 0) => ({
  id, name, parentId, childCount, index: 0, activeSelf: true, activeInHierarchy: true, prefab: 'none' as const, scene: 'Scene',
})
export const tree: SceneTree = {
  epoch: 'a', version: 1, scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: [1] }],
  nodes: [node(1, 'Root', null, 1), { ...node(-2, 'Prefab', 1, 1), prefab: 'root' }, { ...node(3, 'Hidden child', -2), activeInHierarchy: false }],
  total: 3, truncated: false,
}
const base = { unityStatus: 'connected' as const, tree, loading: false, error: null, stale: false, selectedId: null, onSelect: vi.fn(), onConnect: vi.fn() }
beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

it('expands, collapses, keeps ancestors in a filter, and preserves integer ids', () => {
  const select = vi.fn()
  const view = render(<HierarchyPanel {...base} onSelect={select} />)
  expect(screen.queryByText('Hidden child')).toBeNull()
  expect(screen.getByText('Prefab').closest('.hr')?.className).toContain('is-pf')
  fireEvent.click(screen.getByRole('button', { name: 'Prefab' }))
  expect(screen.getByText('Hidden child').closest('.hr')?.className).toContain('is-off')
  fireEvent.click(screen.getByText('Prefab'))
  expect(select).toHaveBeenCalledWith(-2)
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'ArrowLeft' })
  expect(screen.queryByText('Hidden child')).toBeNull()
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'ArrowRight' })
  expect(screen.getByText('Hidden child')).toBeTruthy()
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'ArrowDown' })
  fireEvent.keyDown(screen.getByRole('tree'), { key: 'Enter' })
  expect(select).toHaveBeenLastCalledWith(3)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'HIDDEN' } })
  expect(screen.getByRole('treeitem', { name: 'Root' })).toBeTruthy()
  expect(screen.getByRole('treeitem', { name: 'Prefab' })).toBeTruthy()
  expect(screen.getByRole('treeitem', { name: 'Hidden child' })).toBeTruthy()
  view.unmount()
})

it('shows the live sidebar gate, default Chats, waiting badge, and notlinked state', () => {
  writeSceneEditor(true)
  const connect = vi.fn()
  const props: any = { isSidebarOpen: true, conversations: [{ id: 1, title: 'Chat' }], activeConvId: 1,
    convStatus: { 1: 'awaiting' }, user: { name: 'Burak' }, unityStatus: 'running', hierarchy: <HierarchyPanel {...base} unityStatus="running" onConnect={connect} />,
    onHierarchyVisible: noop, selectConversation: noop, createNewConversation: noop, workspacePath: null,
    closeWorkspace: noop, setShowSettings: noop }
  render(<Sidebar {...props} />)
  expect(screen.getByRole('tab', { name: 'Sohbetler' }).getAttribute('aria-selected')).toBe('true')
  fireEvent.click(screen.getByRole('tab', { name: 'Hiyerarşi' }))
  expect(screen.getByTestId('sidebar-tab-awaiting')).toBeTruthy()
  expect(screen.getByText("Unity'ye bağlı değil")).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: "Unity'ye bağlan" }))
  expect(connect).toHaveBeenCalledOnce()
})

it('windows a 5000-node scene and displays loading, empty, errors and truncation', () => {
  const large = { ...tree, scenes: [{ ...tree.scenes[0], rootIds: Array.from({ length: 5000 }, (_, id) => id) }],
    nodes: Array.from({ length: 5000 }, (_, id) => node(id, `Object ${id}`, null)), total: 5000 }
  const view = render(<HierarchyPanel {...base} tree={large} />)
  expect(screen.getAllByRole('treeitem').length).toBeLessThan(100)
  view.rerender(<HierarchyPanel {...base} tree={null} loading />)
  expect(screen.getByRole('status').textContent).toContain('Yükleniyor')
  view.rerender(<HierarchyPanel {...base} tree={{ ...tree, nodes: [], total: 0 }} />)
  expect(screen.getByText('Sahne boş')).toBeTruthy()
  view.rerender(<HierarchyPanel {...base} error={504} tree={{ ...tree, truncated: true }} />)
  expect(screen.getByRole('alert')).toBeTruthy()
  expect(screen.getByText('Ağacın bir bölümü gösteriliyor.')).toBeTruthy()
})

it('a hierarchy click selects in Unity, reveals Inspector, and keeps expansion across sidebar tabs', async () => {
  writeSceneEditor(true)
  const fetcher = vi.fn(async (url: string, _options?: RequestInit) => ({ ok: true, status: 200,
    json: async () => url.endsWith('tree') ? tree : url.includes('inspect') ? {
      node: { id: -2, name: 'Prefab', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'root', globalId: 'g' }, groups: [], truncated: false,
    } : { epoch: 'a', scene: 1, selection: 0, selectedId: null, compiling: false, playing: false } }))
  vi.stubGlobal('fetch', fetcher)
  function Editor() {
    const [hierarchyVisible, setHierarchyVisible] = React.useState(false)
    const [open, setOpen] = React.useState(false)
    const [tab, setTab] = React.useState<'sahne' | 'dosyalar' | 'kod' | 'onizleme'>('dosyalar')
    const data = useSceneEditor({ api: 'api', token: 't', editorOn: true, unityStatus: 'connected', hierarchyVisible, inspectorVisible: open && tab === 'sahne' })
    const props: any = { isSidebarOpen: true, conversations: [], user: { name: 'Burak' }, unityStatus: 'connected',
      selectConversation: noop, createNewConversation: noop, closeWorkspace: noop, setShowSettings: noop }
    return <><Sidebar {...props} onHierarchyVisible={setHierarchyVisible}
      hierarchy={<HierarchyPanel {...base} tree={data.tree} loading={data.loading} selectedId={data.selectedId}
        onSelect={id => { data.select(id); setOpen(true); setTab('sahne') }} />} />
      <Workspace editorOn open={open} tab={tab} width="dar" onTab={setTab} onWidth={noop} onClose={() => setOpen(false)} drawer={null}
        panes={{ sahne: <InspectorPane inspection={data.inspection} loading={data.inspectLoading} error={data.inspectError} stale={data.stale} />,
          dosyalar: null, kod: null, onizleme: null }} /></>
  }
  render(<Editor />)
  fireEvent.click(screen.getByRole('tab', { name: 'Hiyerarşi' }))
  await screen.findByRole('treeitem', { name: 'Prefab' })
  fireEvent.click(screen.getByRole('button', { name: 'Prefab' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Sohbetler' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Hiyerarşi' }))
  expect(screen.getByRole('treeitem', { name: 'Hidden child' })).toBeTruthy()
  fireEvent.click(screen.getByRole('treeitem', { name: 'Prefab' }))
  expect(screen.getByTestId('workspace').hidden).toBe(false)
  expect(screen.getByRole('tab', { name: 'Inspector' }).getAttribute('aria-selected')).toBe('true')
  await screen.findByDisplayValue('Prefab')
  expect(screen.getByRole('treeitem', { name: 'Prefab' }).getAttribute('aria-selected')).toBe('true')
  const post = fetcher.mock.calls.find(([url]) => url.endsWith('select'))!
  expect(post[1]?.body).toBe('{"id":-2}')
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('inspect/-2'))).toBe(true)
})

it('polling pauses with document visibility, rejects late data, and never overlaps version requests', async () => {
  vi.useFakeTimers()
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  const pending: ((response: Response) => void)[] = []
  const fetcher = vi.fn((_url: string, _options?: RequestInit) => new Promise<Response>(resolve => pending.push(resolve)))
  vi.stubGlobal('fetch', fetcher)
  const h = renderHook(() => useSceneEditor({ api: 'api', token: 't', editorOn: true, unityStatus: 'connected', hierarchyVisible: true, inspectorVisible: false }))
  expect(fetcher).toHaveBeenCalledTimes(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  visibility.mockReturnValue('hidden')
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(fetcher.mock.calls.every(([, options]) => options?.signal?.aborted)).toBe(true)
  await act(async () => { pending[0]({ ok: true, status: 200, json: async () => tree } as Response) })
  expect(h.result.current.tree).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  h.unmount()
})

it('selects via POST, inspects, polls only changes, and aborts when hidden or unmounted', async () => {
  vi.useFakeTimers()
  let scene = 1, epoch = 'a'
  const fetcher = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.endsWith('tree') ? tree
    : url.includes('inspect') ? { node: { ...tree.nodes[1], tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, globalId: 'g' }, groups: [], truncated: false }
      : { epoch, scene, selection: 0, selectedId: null, compiling: false, playing: false } }))
  vi.stubGlobal('fetch', fetcher)
  const opts = { api: 'api', token: 'secret', editorOn: true, unityStatus: 'connected' as const, hierarchyVisible: true, inspectorVisible: false }
  const h = renderHook(props => useSceneEditor(props), { initialProps: opts })
  await act(async () => {})
  act(() => h.result.current.select(-2))
  await act(async () => {})
  const post = fetcher.mock.calls.find(call => call[0].endsWith('select')) as unknown as [string, RequestInit]
  expect(post[1].body).toBe('{"id":-2}')
  expect(post[1].headers).toMatchObject({ 'X-Session-Token': 'secret' })
  expect(h.result.current.inspection?.node.id).toBe(-2)
  const trees = () => fetcher.mock.calls.filter(call => call[0].endsWith('tree')).length
  expect(trees()).toBe(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(trees()).toBe(1)
  scene = 2
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(trees()).toBe(2)
  epoch = 'b'
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(trees()).toBe(3)
  const calls = fetcher.mock.calls.length
  h.rerender({ ...opts, hierarchyVisible: false })
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(fetcher.mock.calls.length).toBe(calls)
  expect((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].signal?.aborted).toBe(true)
  h.rerender(opts)
  await act(async () => {})
  h.unmount()
  const stopped = fetcher.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(fetcher.mock.calls.length).toBe(stopped)
})
