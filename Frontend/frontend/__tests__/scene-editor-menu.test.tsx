import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { HierarchyPanel } from '../renderer/components/home/HierarchyPanel'
import { createMenuEntries } from '../renderer/components/home/SceneContextMenu'
import { useSceneEditor } from '../renderer/hooks/home/useSceneEditor'
import type { SceneNode, SceneTree } from '../renderer/lib/sceneEditor'

const node = (id: number, name: string, parentId: number | null, childCount = 0): SceneNode => ({
  id, name, parentId, childCount, index: 0, activeSelf: true, activeInHierarchy: true, prefab: 'none', scene: 'Scene',
})
const MENU = [
  { item: 'empty', category: '', label: 'Create Empty' },
  { item: 'cube', category: '3D Object', label: 'Cube' },
  { item: 'square', category: 'Light', label: 'Freeform Light 2D/Square' },
  { item: 'point', category: 'Light', label: 'Point Light' },
  { item: 'camera', category: '', label: 'Camera' },
]
type Reply = { status: number; body: unknown }
let nodes: SceneNode[]
let hierarchy: number
let nextReply: Reply | null
let calls: { url: string; body: any }[]
let token = 0

function fetcher(url: string, options?: RequestInit) {
  const body = options?.body ? JSON.parse(String(options.body)) : undefined
  calls.push({ url, body })
  const reply = (status: number, data: unknown) => Promise.resolve({ ok: status < 400, status, json: async () => data } as Response)
  const route = url.replace(/^api\/scene-editor\//, '')
  if (route === 'version') return reply(200, { epoch: 'a', scene: hierarchy, hierarchy, props: 0, selection: 0, selectedId: null, compiling: false, playing: false })
  if (route === 'tree') return reply(200, { epoch: 'a', version: hierarchy, total: nodes.length, truncated: false, nodes,
    scenes: [{ name: 'Scene', path: 'scene', isDirty: false, isLoaded: true, isActive: true, rootIds: nodes.filter(n => n.parentId === null).map(n => n.id) }] } satisfies SceneTree)
  if (route === 'create-menu') return reply(200, { items: MENU })
  if (route.startsWith('inspect/')) return reply(200, { groups: [], truncated: false,
    node: { id: Number(route.slice(8)), name: 'x', activeSelf: true, tag: 'Untagged', layer: { index: 0, name: 'Default' }, isStatic: false, prefab: 'none', globalId: 'g' } })
  if (route === 'select') return reply(200, {})
  if (nextReply) { const r = nextReply; nextReply = null; return reply(r.status, r.body) }
  hierarchy += 1
  if (route === 'create' || route === 'duplicate') {
    const made = node(10, route === 'create' ? 'GameObject' : 'Root (1)', route === 'create' ? body.parentId : null)
    nodes = [...nodes, made]
    return reply(200, { id: 10, name: made.name })
  }
  if (route === 'rename') { nodes = nodes.map(n => n.id === body.id ? { ...n, name: body.name } : n); return reply(200, body) }
  if (route === 'delete') { nodes = nodes.filter(n => n.id !== body.id); return reply(200, { id: body.id }) }
  return reply(404, { detail: 'not_found' })
}

function Harness() {
  const data = useSceneEditor({ api: 'api', token: `t${token}`, editorOn: true, unityStatus: 'connected', hierarchyVisible: true, inspectorVisible: false })
  return <><input aria-label="outside" />
    <HierarchyPanel unityStatus="connected" tree={data.tree} loading={data.loading} error={data.error} stale={data.stale}
      selectedId={data.selectedId} onSelect={data.select} onConnect={() => {}} actions={data} /></>
}
const posts = (route: string) => calls.filter(c => c.url === `api/scene-editor/${route}`).map(c => c.body)
const menus = () => screen.queryAllByRole('menu')
async function setup() {
  render(<Harness />)
  await screen.findByRole('treeitem', { name: 'Root' })
}

beforeEach(() => {
  token += 1
  nodes = [node(1, 'Root', null, 1), node(2, 'Child', 1)]
  hierarchy = 1; nextReply = null; calls = []
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.stubGlobal('fetch', vi.fn(fetcher))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('opens on right-click for that object, selects it, and Esc closes it with focus back on the tree', async () => {
  await setup()
  fireEvent.contextMenu(screen.getByText('Child'))
  const menu = screen.getByRole('menu', { name: 'Nesne menüsü' })
  expect(within(menu).getByRole('menuitem', { name: /Yeniden adlandır/ })).toBeTruthy()
  expect(within(menu).getByRole('menuitem', { name: /Çoğalt/ })).toBeTruthy()
  expect(within(menu).getByRole('menuitem', { name: /Sil/ })).toBeTruthy()
  expect(within(menu).getByText('Child içine oluştur')).toBeTruthy()
  expect(screen.getByRole('treeitem', { name: 'Child' }).getAttribute('aria-selected')).toBe('true')
  expect(posts('select')).toContainEqual({ id: 2 })
  await within(menu).findByRole('menuitem', { name: 'Light' })
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  expect(menus()).toHaveLength(0)
  expect(document.activeElement).toBe(screen.getByRole('tree'))
})

it('opens from Shift+F10 and the context-menu key on the focused row', async () => {
  await setup()
  const tree = screen.getByRole('tree')
  tree.focus()
  fireEvent.keyDown(tree, { key: 'F10', shiftKey: true })
  expect(screen.getByRole('menu').textContent).toContain('Root içine oluştur')
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  fireEvent.keyDown(tree, { key: 'ArrowDown' })
  fireEvent.keyDown(tree, { key: 'ContextMenu' })
  expect(screen.getByRole('menu').textContent).toContain('Child içine oluştur')
  expect(posts('select')).toContainEqual({ id: 2 })
})

it('creates through nested submenus with the row as parent; Esc closes only the topmost menu', async () => {
  await setup()
  fireEvent.contextMenu(screen.getByText('Root'))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Light' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Freeform Light 2D' }))
  expect(menus()).toHaveLength(3)
  fireEvent.keyDown(document.body, { key: 'Escape' })
  expect(menus()).toHaveLength(2)
  fireEvent.click(screen.getByRole('menuitem', { name: 'Freeform Light 2D' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Square' }))
  expect(menus()).toHaveLength(0)
  await waitFor(() => expect(posts('create')).toEqual([{ item: 'square', parentId: 1 }]))
})

it('empty space offers only Create with the scene root as parent, then selects and names the new object promptly', async () => {
  await setup()
  fireEvent.contextMenu(document.querySelector('.hier-body')!)
  const menu = screen.getByRole('menu')
  expect(within(menu).queryByRole('menuitem', { name: /Yeniden adlandır/ })).toBeNull()
  expect(within(menu).getByText('Sahneye oluştur')).toBeTruthy()
  const written = calls.length
  fireEvent.click(await within(menu).findByRole('menuitem', { name: 'Create Empty' }))
  await waitFor(() => expect(posts('create')).toEqual([{ item: 'empty', parentId: null }]))
  // The write triggers a version poll at once, well inside the 1 s tick.
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Yeni ad' })).toBeTruthy(), { timeout: 600 })
  expect(calls.slice(written).some(c => c.url.endsWith('/version'))).toBe(true)
  expect(posts('select')).toContainEqual({ id: 10 })
  expect(screen.getByRole('treeitem', { name: 'Root' })).toBeTruthy()
})

it('builds Unity order: Create Empty, categories, then Camera', () => {
  const entries = createMenuEntries(MENU, () => {})
  expect(entries.map(e => e.kind === 'item' ? e.label : '')).toEqual(['Create Empty', '3D Object', 'Light', 'Camera'])
})

it('menu keyboard: arrows, Right into a submenu, Left back, Enter runs', async () => {
  await setup()
  fireEvent.contextMenu(document.querySelector('.hier-body')!)
  await screen.findByRole('menuitem', { name: 'Light' })
  const key = (k: string) => fireEvent.keyDown(document.activeElement!, { key: k })
  key('ArrowDown'); key('ArrowDown'); key('ArrowDown')
  expect(screen.getByRole('menuitem', { name: 'Light' }).className).toContain('is-hot')
  key('ArrowRight')
  expect(menus()).toHaveLength(2)
  key('ArrowLeft')
  expect(menus()).toHaveLength(1)
  key('ArrowRight'); key('ArrowDown'); key('Enter')
  await waitFor(() => expect(posts('create')).toEqual([{ item: 'point', parentId: null }]))
})

it('renames inline: Enter commits, Esc cancels, blur commits', async () => {
  await setup()
  const tree = screen.getByRole('tree')
  tree.focus()
  fireEvent.keyDown(tree, { key: 'F2' })
  let input = screen.getByRole('textbox', { name: 'Yeni ad' }) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'Nope' } })
  fireEvent.keyDown(input, { key: 'Escape' })
  expect(screen.queryByRole('textbox', { name: 'Yeni ad' })).toBeNull()
  expect(posts('rename')).toEqual([])
  expect(document.activeElement).toBe(tree)

  fireEvent.keyDown(tree, { key: 'F2' })
  input = screen.getByRole('textbox', { name: 'Yeni ad' }) as HTMLInputElement
  fireEvent.keyDown(input, { key: 'Delete' })
  expect(posts('delete')).toEqual([])
  fireEvent.change(input, { target: { value: '  Arena  ' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(posts('rename')).toEqual([{ id: 1, name: 'Arena' }]))
  await screen.findByRole('treeitem', { name: 'Arena' })

  fireEvent.contextMenu(screen.getByText('Arena'))
  fireEvent.click(screen.getByRole('menuitem', { name: /Yeniden adlandır/ }))
  input = screen.getByRole('textbox', { name: 'Yeni ad' }) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'Stadium' } })
  fireEvent.blur(input)
  await waitFor(() => expect(posts('rename')).toEqual([{ id: 1, name: 'Arena' }, { id: 1, name: 'Stadium' }]))
})

it('Delete and Cmd/Ctrl+D act on the focused tree only', async () => {
  await setup()
  fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Delete' })
  fireEvent.keyDown(screen.getByLabelText('outside'), { key: 'd', ctrlKey: true })
  const tree = screen.getByRole('tree')
  tree.focus()
  fireEvent.keyDown(tree, { key: 'd', metaKey: true })
  await waitFor(() => expect(posts('duplicate')).toEqual([{ id: 1 }]))
  await waitFor(() => expect(screen.getByRole('treeitem', { name: 'Root (1)' }).getAttribute('aria-selected')).toBe('true'))
  fireEvent.keyDown(tree, { key: 'ArrowUp' })
  fireEvent.keyDown(tree, { key: 'Home' })
  fireEvent.keyDown(tree, { key: 'Delete' })
  await waitFor(() => expect(posts('delete')).toEqual([{ id: 1 }]))
  await waitFor(() => expect(screen.queryByRole('treeitem', { name: 'Root' })).toBeNull())
  expect(screen.queryAllByRole('treeitem').some(row => row.getAttribute('aria-selected') === 'true')).toBe(false)
})

it('a 409 shows the quiet inline reason; retryable codes offer Try again', async () => {
  await setup()
  const tree = screen.getByRole('tree')
  tree.focus()
  nextReply = { status: 409, body: { detail: 'prefab_part' } }
  fireEvent.keyDown(tree, { key: 'Delete' })
  const line = await screen.findByRole('alert')
  expect(line.textContent).toContain("prefab'ın parçası")
  expect(within(line).queryByRole('button', { name: 'Tekrar dene' })).toBeNull()
  expect(screen.getByRole('treeitem', { name: 'Root' })).toBeTruthy()

  nextReply = { status: 409, body: { detail: 'compiling' } }
  fireEvent.keyDown(tree, { key: 'd', ctrlKey: true })
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('derleniyor'))
  fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Tekrar dene' }))
  await waitFor(() => expect(posts('duplicate')).toEqual([{ id: 1 }, { id: 1 }]))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})
