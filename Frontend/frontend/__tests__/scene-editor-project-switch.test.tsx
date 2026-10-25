import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'

const tree = { epoch: 'a', version: 1, nodes: [{ id: 1, name: 'Old project' }], scenes: [], total: 1, truncated: false }
const inspection = { node: { id: 1, name: 'Old object' }, groups: [], truncated: false }
const opts = { api: 'api', token: 't', editorOn: true, unityStatus: 'connected' as const,
  hierarchyVisible: true, inspectorVisible: true, projectPath: 'GameA' }
let version: number
let treeStatus: number
let inspectStatus: number
let versionStatus: number
let holdVersion: boolean
const message = 'Unity has a different project open'

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  version = 1; treeStatus = 200; inspectStatus = 200; versionStatus = 200; holdVersion = false
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('version') && holdVersion) return new Promise(() => {})
    const status = url.endsWith('tree') ? treeStatus : url.includes('inspect') ? inspectStatus : url.endsWith('version') ? versionStatus : 200
    return { ok: status === 200, status, json: async () => status === 409
      ? { detail: { code: 'project_mismatch', message } }
      : url.endsWith('tree') ? tree : url.includes('inspect') ? inspection
        : { epoch: 'a', scene: version, selection: 0, selectedId: null, compiling: false, playing: false } }
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

it('clears the tree, inspection, selection and errors when the project changes, rejecting old reads', async () => {
  const h = renderHook(props => useSceneEditor(props), { initialProps: opts })
  await act(async () => {})
  expect(h.result.current.tree).toEqual(tree)
  act(() => h.result.current.select(1))
  await act(async () => {})
  expect(h.result.current.inspection).toEqual(inspection)
  holdVersion = true
  h.rerender({ ...opts, projectPath: 'GameB' })
  expect(h.result.current.tree).toBeNull()
  expect(h.result.current.inspection).toBeNull()
  expect(h.result.current.selectedId).toBeNull()
  expect(h.result.current.error).toBeNull()
  expect(h.result.current.projectMismatch).toBeNull()
})

it.each([409, 500])('clears a last good tree only for project mismatch (status %s)', async status => {
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  expect(h.result.current.tree).toEqual(tree)
  version += 1; treeStatus = status
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(h.result.current.error).toBe(status)
  expect(h.result.current.tree).toEqual(status === 409 ? null : tree)
  if (status === 409) {
    expect(h.result.current.projectMismatch).toBe(message)
    treeStatus = 200
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(h.result.current.tree).toEqual(tree)
    expect(h.result.current.projectMismatch).toBeNull()
  }
})

it.each([409, 500])('clears a last good inspection only for project mismatch (status %s)', async status => {
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  act(() => h.result.current.select(1))
  await act(async () => {})
  expect(h.result.current.inspection).toEqual(inspection)
  version += 1; inspectStatus = status
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(h.result.current.inspectError).toBe(status)
  expect(h.result.current.inspection).toEqual(status === 409 ? null : inspection)
})

it.each([409, 500])('clears the last good tree and inspection only when version reports a project mismatch (status %s)', async status => {
  const h = renderHook(() => useSceneEditor(opts))
  await act(async () => {})
  act(() => h.result.current.select(1))
  await act(async () => {})
  expect(h.result.current.tree).toEqual(tree)
  expect(h.result.current.inspection).toEqual(inspection)
  const reads = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('tree') || String(url).includes('inspect')).length
  const before = reads()
  versionStatus = status
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(reads()).toBe(before)
  expect(h.result.current.error).toBe(status)
  expect(h.result.current.projectMismatch).toBe(status === 409 ? message : null)
  expect(h.result.current.tree).toEqual(status === 409 ? null : tree)
  expect(h.result.current.inspection).toEqual(status === 409 ? null : inspection)
  if (status === 409) {
    versionStatus = 200
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(h.result.current.tree).toEqual(tree)
    expect(h.result.current.inspection).toEqual(inspection)
  }
})

it('resets both loading indicators when switching projects with reads in flight', async () => {
  const fetcher = vi.mocked(fetch)
  const original = fetcher.getMockImplementation()!
  const signals: AbortSignal[] = []
  fetcher.mockImplementation((url, options) => {
    if (String(url).endsWith('tree') || String(url).includes('inspect')) {
      signals.push(options!.signal!)
      return new Promise(() => {})
    }
    return original(url, options)
  })
  const h = renderHook(props => useSceneEditor(props), { initialProps: opts })
  await act(async () => {})
  act(() => h.result.current.select(1))
  expect(h.result.current.loading).toBe(true)
  expect(h.result.current.inspectLoading).toBe(true)
  holdVersion = true
  h.rerender({ ...opts, projectPath: 'GameB' })
  expect(signals).toHaveLength(2)
  expect(signals.every(signal => signal.aborted)).toBe(true)
  expect(h.result.current.loading).toBe(false)
  expect(h.result.current.inspectLoading).toBe(false)
})
