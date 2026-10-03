import React, { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { HierarchyPanel } from '../renderer/components/home/HierarchyPanel'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import type { UnityMCPStatus } from '../renderer/hooks/home/useAIConfig'
import type { SceneNode, SceneTree } from '../renderer/lib/sceneEditor'

const node = (id: number, name: string, parentId: number | null, childCount = 0): SceneNode => ({
  id, name, parentId, childCount, index: 0, activeSelf: true, activeInHierarchy: true, prefab: 'none', scene: 'Scene',
})
type Reply = { status: number; body: unknown } | 'reject'
let nodes: SceneNode[]
let hierarchy: number
let replies: Reply[]
let calls: { url: string; body: any }[]
let token = 0
let epoch: string
let gate: Promise<void> | null
let editor: ReturnType<typeof useSceneEditor>
let setStatus: (status: UnityMCPStatus) => void
let setEditorOn: (on: boolean) => void

async function fetcher(url: string, options?: RequestInit) {
  const body = options?.body ? JSON.parse(String(options.body)) : undefined
  calls.push({ url, body })
  const reply = (status: number, data: unknown) => Promise.resolve({ ok: status < 400, status, json: async () => data } as Response)
  const route = url.replace(/^api\/scene-editor\//, '')
  if (route === 'version') return reply(200, { epoch, scene: hierarchy, hierarchy, props: 0, selection: 0, selectedId: null, compiling: false, playing: false })
  if (route === 'tree') return reply(200, { epoch, version: hierarchy, total: nodes.length, truncated: false, nodes,
    scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: nodes.filter(n => n.parentId === null).map(n => n.id) }] } satisfies SceneTree)
  if (route.startsWith('inspect/')) return reply(200, { groups: [], truncated: false,
    node: { id: Number(route.slice(8)), name: 'x', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: 'g' } })
  if (route === 'select' || route === 'create-menu') return reply(200, { items: [{ item: 'camera', category: '', label: 'Camera' }] })
  if (gate) await gate
  const next = replies.shift()
  if (next === 'reject') throw new TypeError('Failed to fetch')
  if (next) return reply(next.status, next.body)
  hierarchy += 1
  if (route === 'create' || route === 'duplicate') {
    const made = node(10, route === 'create' ? 'GameObject' : 'Copy', null)
    nodes = [...nodes, made]
    return reply(200, { id: 10, name: made.name })
  }
  if (route === 'rename') { nodes = nodes.map(n => n.id === body.id ? { ...n, name: body.name } : n); return reply(200, body) }
  if (route === 'delete') { nodes = nodes.filter(n => n.id !== body.id); return reply(200, { id: body.id }) }
  return reply(404, { detail: 'not_found' })
}

function Harness() {
  const [unityStatus, status] = useState<UnityMCPStatus>('connected')
  const [editorOn, on] = useState(true)
  setStatus = status; setEditorOn = on
  editor = useSceneEditor({ api: 'api', token: `q${token}`, editorOn, unityStatus, hierarchyVisible: true, inspectorVisible: false })
  return <HierarchyPanel unityStatus={unityStatus} tree={editor.tree} loading={editor.loading} error={editor.error} stale={editor.stale}
    selectedId={editor.selectedId} onSelect={editor.select} onConnect={() => {}} actions={editor} />
}
const posts = (route: string) => calls.filter(c => c.url === `api/scene-editor/${route}`).map(c => c.body)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
const selectedName = () => screen.queryAllByRole('treeitem').find(row => row.getAttribute('aria-selected') === 'true')?.getAttribute('aria-label') ?? null
let release: () => void
const hold = () => { gate = new Promise(r => { release = () => { gate = null; r() } }) }

async function setup(first = 'Root') {
  const view = render(<Harness />)
  await screen.findByRole('treeitem', { name: first })
  return view
}
async function pick(name: string) {
  fireEvent.click(screen.getByText(name))
  await waitFor(() => expect(selectedName()).toBe(name))
}

beforeEach(() => {
  token += 1
  nodes = [node(1, 'Root', null), node(2, 'Light', null), node(3, 'Player', null)]
  hierarchy = 1; replies = []; calls = []; epoch = 'a'; gate = null
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.stubGlobal('fetch', vi.fn(fetcher))
})
afterEach(() => { release?.(); cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('R2: a Unity epoch change drops queued writes and the in-flight result selects nothing', async () => {
  await setup()
  await pick('Root')
  hold()
  act(() => { void editor.duplicate(1); void editor.rename(2, 'Lamp'); void editor.remove(3) })
  await waitFor(() => expect(posts('duplicate')).toEqual([{ id: 1 }]))
  epoch = 'b'
  const before = calls.length
  await waitFor(() => expect(calls.slice(before).some(c => c.url.endsWith('/tree'))).toBe(true), { timeout: 2500 })
  release()
  await screen.findByRole('treeitem', { name: 'Copy' })
  await wait(50)
  expect(posts('rename')).toEqual([])
  expect(posts('delete')).toEqual([])
  expect(posts('select')).not.toContainEqual({ id: 10 })
  expect(selectedName()).not.toBe('Copy')
}, 6000)

it.each([
  ['Unity disconnects', () => setStatus('off')],
  ['the editor is turned off', () => setEditorOn(false)],
])('R2: queued writes are dropped when %s', async (_, stop) => {
  await setup()
  hold()
  act(() => { void editor.rename(1, 'A'); void editor.rename(2, 'B'); void editor.remove(3) })
  await waitFor(() => expect(posts('rename')).toEqual([{ id: 1, name: 'A' }]))
  act(() => stop())
  release()
  await wait(50)
  expect(posts('rename')).toEqual([{ id: 1, name: 'A' }])
  expect(posts('delete')).toEqual([])
})

it('R2: queued writes are dropped on unmount', async () => {
  const view = await setup()
  hold()
  act(() => { void editor.rename(1, 'A'); void editor.rename(2, 'B') })
  await waitFor(() => expect(posts('rename')).toHaveLength(1))
  view.unmount()
  release()
  await wait(50)
  expect(posts('rename')).toEqual([{ id: 1, name: 'A' }])
})

it('R3: a held arrow key moves the UI at once but syncs Unity a handful of times, ending on the last row', async () => {
  nodes = Array.from({ length: 40 }, (_, i) => node(i + 1, `N${i + 1}`, null))
  await setup('N1')
  const tree = screen.getByRole('tree')
  tree.focus()
  await wait(200)
  const before = calls.length
  const inspects = () => calls.slice(before).filter(c => c.url.includes('/inspect/')).map(c => c.url.split('/').pop())
  for (let i = 0; i < 30; i++) {
    fireEvent.keyDown(tree, { key: 'ArrowDown', repeat: i > 0 })
    expect(selectedName()).toBe(`N${i + 2}`)
    await wait(33)
  }
  await wait(300)
  const synced = calls.slice(before).filter(c => c.url.endsWith('/select')).map(c => c.body)
  expect(synced.length).toBeLessThanOrEqual(5)
  expect(synced.at(-1)).toEqual({ id: 31 })
  expect(inspects().length).toBeLessThanOrEqual(5)
  expect(inspects().at(-1)).toBe('31')
  expect(selectedName()).toBe('N31')
}, 6000)

it.each([
  ['503 on duplicate', { status: 503, body: { detail: 'unity_unavailable' } } as Reply, 'duplicate'],
  ['a rejected fetch on duplicate', 'reject' as Reply, 'duplicate'],
  ['503 on create', { status: 503, body: { detail: 'unity_unavailable' } } as Reply, 'create'],
  ['a rejected fetch on create', 'reject' as Reply, 'create'],
])('R4: %s offers no retry and refreshes the tree', async (_, failure, route) => {
  await setup()
  await pick('Root')
  replies = [failure]
  const before = calls.length
  act(() => { void (route === 'create' ? editor.create('camera', null) : editor.duplicate(1)) })
  const line = await screen.findByRole('alert')
  expect(line.textContent).toContain("Unity'ye ulaşılamadı.")
  expect(line.textContent).toContain('Ağaç yenilendi')
  expect(within(line).queryByRole('button', { name: 'Tekrar dene' })).toBeNull()
  await waitFor(() => expect(calls.slice(before).some(c => c.url.endsWith('/version'))).toBe(true), { timeout: 500 })
})

it('R4: rename keeps its retry after 503 and after a rejected fetch', async () => {
  await setup()
  for (const failure of [{ status: 503, body: {} }, 'reject'] as Reply[]) {
    replies = [failure]
    act(() => { void editor.rename(1, 'Arena') })
    await waitFor(() => expect(within(screen.getByRole('alert')).getByRole('button', { name: 'Tekrar dene' })).toBeTruthy())
    expect(screen.getByRole('alert').textContent).not.toContain('Ağaç yenilendi')
    act(() => editor.clearWriteError())
  }
})

it('R5: a late create result is not selected once the user selected something else', async () => {
  await setup()
  await pick('Root')
  hold()
  act(() => { void editor.create('camera', null) })
  await waitFor(() => expect(posts('create')).toHaveLength(1))
  await pick('Player')
  release()
  await screen.findByRole('treeitem', { name: 'GameObject' })
  await wait(50)
  expect(selectedName()).toBe('Player')
  expect(posts('select')).not.toContainEqual({ id: 10 })
  expect(screen.queryByRole('textbox', { name: 'Yeni ad' })).toBeNull()
})

it('R5: an untouched selection still moves to the created object', async () => {
  await setup()
  await pick('Root')
  act(() => { void editor.create('camera', null) })
  await waitFor(() => expect(selectedName()).toBe('GameObject'))
})

it('R5: a queued write that succeeds keeps the earlier write\'s error', async () => {
  await setup()
  hold()
  replies = [{ status: 409, body: { detail: 'compiling' } }]
  act(() => { void editor.duplicate(1); void editor.rename(2, 'Lamp') })
  release()
  await waitFor(() => expect(posts('rename')).toEqual([{ id: 2, name: 'Lamp' }]))
  await screen.findByRole('treeitem', { name: 'Lamp' })
  expect(screen.getByRole('alert').textContent).toContain('derleniyor')
  // Its own retry succeeding clears it.
  fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Tekrar dene' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})

it('R5: the busy notice stays while the queue is full and goes once it drains', async () => {
  await setup()
  hold()
  act(() => { for (let i = 0; i < 9; i++) void editor.rename(1, `R${i}`) })
  expect(screen.getByRole('alert').textContent).toContain('Önceki düzenlemeler sürüyor')
  await wait(30)
  expect(screen.getByRole('alert').textContent).toContain('Önceki düzenlemeler sürüyor')
  release()
  await waitFor(() => expect(posts('rename')).toHaveLength(8))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})

it('R5: a second Delete of the same object while the first waits queues nothing', async () => {
  await setup()
  await pick('Light')
  const tree = screen.getByRole('tree')
  hold()
  fireEvent.keyDown(tree, { key: 'Delete' })
  await waitFor(() => expect(posts('delete')).toEqual([{ id: 2 }]))
  fireEvent.keyDown(tree, { key: 'Delete' })
  release()
  await waitFor(() => expect(screen.queryByRole('treeitem', { name: 'Light' })).toBeNull())
  await wait(50)
  expect(posts('delete')).toEqual([{ id: 2 }])
  expect(screen.queryByRole('alert')).toBeNull()
})
