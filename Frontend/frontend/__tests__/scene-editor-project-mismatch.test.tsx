import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HierarchyPanel } from '../renderer/components/home/HierarchyPanel'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import { loadFailure, postSceneWrite, writeErrorCode } from '../renderer/lib/sceneEditor'
import { en, tr } from '../renderer/lib/i18n'

const MESSAGE = "Bu istek Unity'de açık olan projeye (GameB) gidiyor, Gamachine'de açık proje ise GameA; istek gönderilmedi. Unity'de ve Gamachine'de aynı projeyi aç."
const DETAIL = { code: 'project_mismatch', message: MESSAGE }
let mismatch: boolean
let token = 0

const reply = (status: number, data: unknown) => Promise.resolve({ ok: status < 400, status, json: async () => data } as Response)
async function fetcher(url: string) {
  const route = url.replace(/^api\/scene-editor\//, '')
  if (mismatch) return reply(409, { detail: DETAIL })
  if (route === 'version') return reply(200, { epoch: 'a', scene: 1, hierarchy: 1, props: 0, selection: 0, selectedId: null, compiling: false, playing: false })
  if (route === 'tree') return reply(200, { epoch: 'a', version: 1, total: 1, truncated: false,
    nodes: [{ id: 1, name: 'Root', parentId: null, index: 0, activeSelf: true, activeInHierarchy: true, childCount: 0, prefab: 'none', scene: 'Scene' }],
    scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: [1] }] })
  if (route === 'rename') return reply(409, { detail: DETAIL })
  return reply(200, {})
}

function Harness() {
  const data = useSceneEditor({ api: 'api', token: `t${token}`, editorOn: true, unityStatus: 'connected', hierarchyVisible: true, inspectorVisible: false })
  return <HierarchyPanel unityStatus="connected" tree={data.tree} loading={data.loading} error={data.error} stale={data.stale}
    selectedId={data.selectedId} onSelect={data.select} onConnect={() => {}} actions={data} />
}

beforeEach(() => {
  token += 1; mismatch = false
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.stubGlobal('fetch', vi.fn(fetcher))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('maps the structured 409 to project_mismatch and carries the message', async () => {
  expect(writeErrorCode(409, DETAIL)).toBe('project_mismatch')
  expect(writeErrorCode(409, 'locked')).toBe('locked')
  expect(writeErrorCode(409, 'unknown text')).toBe('write_failed')
  const result = await postSceneWrite('api', 't', 'rename', { id: 1, name: 'x' })
  expect(result).toEqual({ ok: false, code: 'project_mismatch', message: MESSAGE })
})

it('reads the message off a failed load', async () => {
  const failure = await loadFailure({ status: 409, json: async () => ({ detail: DETAIL }) } as Response)
  expect(failure).toMatchObject({ status: 409, message: MESSAGE })
  const bare = await loadFailure({ status: 502, json: async () => { throw new Error('no body') } } as unknown as Response)
  expect(bare).toMatchObject({ status: 502, message: undefined })
})

it('shows the message when the tree cannot load because of the project mismatch', async () => {
  mismatch = true
  render(<Harness />)
  expect((await screen.findByRole('alert')).textContent).toBe(MESSAGE)
})

it('shows the message for a refused write', async () => {
  const view = render(<Harness />)
  await screen.findByRole('treeitem', { name: 'Root' })
  fireEvent.contextMenu(screen.getByText('Root'))
  fireEvent.click(await screen.findByRole('menuitem', { name: /Yeniden adlandır/ }))
  const input = await screen.findByRole('textbox')
  fireEvent.change(input, { target: { value: 'Renamed' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(view.container.querySelector('.hier-err-t')?.textContent).toBe(MESSAGE))
})

it('has the short text in both languages', () => {
  expect(tr['sceneEditor.write.project_mismatch']).toBe("Unity'de Gamachine'deki proje açık değil.")
  expect(en['sceneEditor.write.project_mismatch']).toBe("Unity does not have Gamachine's project open.")
})
