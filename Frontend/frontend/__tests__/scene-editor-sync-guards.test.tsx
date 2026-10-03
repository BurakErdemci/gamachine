import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import type { SceneTree } from '../renderer/lib/sceneEditor'

type Unity = { epoch: string; selection: number; selectedId: number | null }
let unity: Unity
// 'apply' selects at once like a healthy Unity; 'hang' never answers until the request is aborted.
let selectMode: 'apply' | 'hang'
let writeReplies: { status: number; body: unknown }[]
let calls: { url: string; body: any }[]

const reply = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body }) as Response
const tree: SceneTree = { epoch: 'a', version: 1, total: 0, truncated: false, nodes: [],
  scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: [] }] }

function fetcher(url: string, options?: RequestInit): Promise<Response> {
  const body = options?.body ? JSON.parse(String(options.body)) : undefined
  calls.push({ url, body })
  const route = url.replace(/^api\/scene-editor\//, '')
  if (route === 'version') return Promise.resolve(reply(200, { ...unity, scene: 1, hierarchy: 1, props: 0, compiling: false, playing: false }))
  if (route === 'tree') return Promise.resolve(reply(200, tree))
  if (route.startsWith('inspect/')) return Promise.resolve(reply(200, { groups: [], truncated: false,
    node: { id: Number(route.slice(8)), name: 'x', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: 'g' } }))
  if (route === 'select') {
    if (selectMode === 'hang') return new Promise((_, reject) => options?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
    unity.selection += 1; unity.selectedId = body.id
    return Promise.resolve(reply(200, {}))
  }
  const next = writeReplies.shift()
  return Promise.resolve(next ? reply(next.status, next.body) : reply(200, body))
}

const opts = { api: 'api', token: 't', editorOn: true, unityStatus: 'connected' as const, hierarchyVisible: true, inspectorVisible: true }
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const posts = (route: string) => calls.filter(c => c.url === `api/scene-editor/${route}`).map(c => c.body)
// The user picks an object in Unity's own Hierarchy window.
const pickInUnity = (id: number) => { unity.selection += 1; unity.selectedId = id }

beforeEach(() => {
  vi.useFakeTimers()
  unity = { epoch: 'a', selection: 0, selectedId: null }
  selectMode = 'apply'; writeReplies = []; calls = []
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.stubGlobal('fetch', vi.fn(fetcher))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

async function mount() {
  const h = renderHook(props => useSceneEditor(props), { initialProps: opts })
  await tick(0)
  return h
}

it('F1: a Unity-side pick made during the select cooldown is adopted once the local select settles', async () => {
  const h = await mount()
  await tick(950)
  act(() => h.result.current.select(1))
  await tick(10)
  pickInUnity(7)
  // The poll at 1 s lands inside the 120 ms cooldown and must not consume the change.
  await tick(40)
  expect(h.result.current.selectedId).toBe(1)
  await tick(1000)
  expect(h.result.current.selectedId).toBe(7)
  // Our own select landing in Unity is not fought: Unity's selectedId equals ours, nothing flips.
  act(() => h.result.current.select(3))
  await tick(3000)
  expect(h.result.current.selectedId).toBe(3)
  expect(posts('select').at(-1)).toEqual({ id: 3 })
})

it('F2: a hung select post is aborted, so a later Unity-side pick is still adopted', async () => {
  const h = await mount()
  selectMode = 'hang'
  act(() => h.result.current.select(1))
  await tick(500)
  pickInUnity(7)
  await tick(4000)
  expect(h.result.current.selectedId).toBe(1)
  await tick(3000)
  expect(h.result.current.selectedId).toBe(7)
})

it('F3: the last local pick survives an effect restart inside the coalescing window', async () => {
  const h = await mount()
  await tick(300)
  act(() => h.result.current.select(1))
  await tick(20)
  act(() => h.result.current.select(2))
  // Both panes hide before the trailing select for 2 is sent, then come back.
  h.rerender({ ...opts, hierarchyVisible: false, inspectorVisible: false })
  await tick(20)
  h.rerender(opts)
  await tick(2500)
  expect(h.result.current.selectedId).toBe(2)
  expect(posts('select').at(-1)).toEqual({ id: 2 })
  expect(unity.selectedId).toBe(2)
  expect(h.result.current.inspection?.node.id).toBe(2)
})

it('F4a: a Unity epoch change clears a write error and its retry', async () => {
  const h = await mount()
  writeReplies = [{ status: 503, body: {} }]
  await act(async () => { await h.result.current.rename(2, 'A') })
  const stale = h.result.current.writeError
  expect(stale?.retry).toBeTypeOf('function')
  unity.epoch = 'b'
  await tick(1000)
  expect(h.result.current.writeError).toBeNull()
  await act(async () => { stale!.retry!(); await vi.advanceTimersByTimeAsync(10) })
  expect(posts('rename')).toEqual([{ id: 2, name: 'A' }])
})

it('F4b: a later success on the same object clears the older error, but a stale retry keeps a newer one', async () => {
  const h = await mount()
  writeReplies = [{ status: 503, body: {} }]
  await act(async () => { await h.result.current.rename(2, 'A') })
  expect(h.result.current.writeError?.retry).toBeTypeOf('function')
  await act(async () => { await h.result.current.rename(2, 'B') })
  expect(h.result.current.writeError).toBeNull()

  writeReplies = [{ status: 503, body: {} }, { status: 503, body: {} }]
  await act(async () => { await h.result.current.rename(2, 'C') })
  const older = h.result.current.writeError!
  await act(async () => { await h.result.current.rename(2, 'D') })
  const newer = h.result.current.writeError!
  expect(newer).not.toBe(older)
  await act(async () => { older.retry!(); await vi.advanceTimersByTimeAsync(10) })
  expect(posts('rename').at(-1)).toEqual({ id: 2, name: 'C' })
  expect(h.result.current.writeError).toBe(newer)
})
