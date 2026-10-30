import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { translations } from '../renderer/lib/i18n'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { suggest, suggestedSites, tabShortcutOf, type HistoryItem } from '../renderer/lib/browserSuggest'
import { BrowserTabs } from '../renderer/components/home/BrowserTabs'
import { tabShortcut } from '../main/helpers/browser-host'

const tr = translations.tr

let listeners: Map<string, (payload: unknown) => void>
let invoke: ReturnType<typeof vi.fn>
let entries: HistoryItem[]
let platform = 'Win32'

const tab = (id: number, over: Record<string, unknown> = {}) =>
  ({ id, url: `https://site${id}.example/`, title: `Site ${id}`, loading: false, favicon: '', agent: false, ...over })
const live = (over: Record<string, unknown> = {}) => {
  const tabs = (over.tabs as any[]) ?? [tab(1, { agent: true }), tab(2)]
  const activeTabId = (over.activeTabId as number) ?? tabs[0].id
  const shown = tabs.find(t => t.id === activeTabId) ?? tabs[0]
  return { active: true, conversationId: 3, url: shown.url, title: shown.title, loading: false, userControl: false, ...over, tabs, activeTabId }
}
const props = () => ({ running: true, onStop: vi.fn(async () => {}), onNeedChat: vi.fn(async () => 9) }) as any
const calls = (op: string) => invoke.mock.calls.filter(c => c[0] === 'browser-live' && c[1]?.op === op).map(c => c[1])
const push = (payload: unknown) => act(() => { listeners.get('browser-live')?.(payload) })
const item = (url: string, title: string, visits = 1, lastVisit = 1_000): HistoryItem => ({ url, title, favicon: '', visits, lastVisit })

async function mount() {
  const view = render(<BrowserPanel visible conversationId={3} {...props()} />)
  await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
  return view
}
const press = (target: Element, key: string, mods: Record<string, boolean> = {}) => {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...mods })
  act(() => { target.dispatchEvent(event) })
  return event
}
const setPlatform = (value: string) => Object.defineProperty(window.navigator, 'platform', { value, configurable: true })

beforeEach(() => {
  listeners = new Map()
  entries = []
  platform = 'Win32'
  setPlatform(platform)
  invoke = vi.fn(async (_channel: string, req: any) => {
    if (req?.op === 'panel-status') return { active: false }
    if (req?.op === 'history') return { entries }
    return { ok: true }
  })
  vi.stubGlobal('ipc', {
    invoke,
    on: vi.fn((channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) }),
  })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const rect = this.classList.contains('bp-view') ? { left: 10, top: 20, width: 300, height: 200 } : { left: 0, top: 0, width: 0, height: 0 }
    return { ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height, x: rect.left, y: rect.top, toJSON() {} } as DOMRect
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  setPlatform('')
  document.body.innerHTML = ''
})

describe('BrowserPanel tab strip', () => {
  it('lists the chat\'s tabs, marks the agent tab and selects the one in front', async () => {
    await mount()
    push(live({ activeTabId: 2 }))
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(t => t.getAttribute('aria-selected'))).toEqual(['false', 'true'])
    const agent = tabs[0].closest('.bp-tab')!
    expect(agent.getAttribute('data-agent')).toBe('true')
    expect(within(agent as HTMLElement).getByLabelText(tr['bp.agentTab'])).toBeTruthy()
    expect(tabs[1].closest('.bp-tab')!.getAttribute('data-agent')).toBe('false')
    expect(tabs[1].closest('.bp-tab')!.querySelector('.bp-tab-agent')).toBeNull()
  })

  it('sends select, close-tab and open from the strip, with shortcut hints per platform', async () => {
    await mount()
    push(live())
    fireEvent.click(screen.getAllByRole('tab')[1])
    expect(calls('select')).toEqual([{ op: 'select', conversationId: 3, tabId: 2 }])
    fireEvent.click(screen.getByRole('button', { name: `${tr['bp.closeTab']}: Site 2` }))
    expect(calls('close-tab')).toEqual([{ op: 'close-tab', conversationId: 3, tabId: 2 }])
    const plus = screen.getByRole('button', { name: tr['bp.newTab'] })
    expect(plus.getAttribute('title')).toContain('Ctrl+T')
    fireEvent.click(plus)
    await waitFor(() => expect(calls('open')).toEqual([{ op: 'open', conversationId: 3 }]))
    cleanup()
    setPlatform('MacIntel')
    await mount()
    push(live())
    expect(screen.getByRole('button', { name: tr['bp.newTab'] }).getAttribute('title')).toContain('⌘T')
  })

  it('a tab without a title shows its site, and a blank one is called new tab', async () => {
    await mount()
    push(live({ tabs: [tab(1, { title: '', url: 'https://www.itch.io/x', agent: true }), tab(2, { title: '', url: 'about:blank' })] }))
    const names = screen.getAllByRole('tab').map(t => t.querySelector('.bp-tab-t')!.textContent)
    expect(names).toEqual(['itch.io', tr['bp.newTab']])
  })
})

describe('BrowserPanel tab shortcuts', () => {
  it('uses Ctrl on Windows: new, close, reopen and select by number, never copy or paste', async () => {
    await mount()
    push(live({ tabs: [tab(1, { agent: true }), tab(2), tab(3)], activeTabId: 2 }))
    const address = screen.getByLabelText(tr['bp.address'])
    expect(press(address, 't', { ctrlKey: true }).defaultPrevented).toBe(true)
    await waitFor(() => expect(calls('open')).toHaveLength(1))
    expect(press(address, 'w', { ctrlKey: true }).defaultPrevented).toBe(true)
    expect(calls('close-tab')).toEqual([{ op: 'close-tab', conversationId: 3, tabId: 2 }])
    expect(press(address, 'T', { ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(true)
    expect(calls('reopen-tab')).toHaveLength(1)
    press(address, '1', { ctrlKey: true })
    press(address, '3', { ctrlKey: true })
    press(address, '9', { ctrlKey: true })
    press(address, '7', { ctrlKey: true })
    expect(calls('select').map(c => c.tabId)).toEqual([1, 3, 3])
    for (const k of ['c', 'v', 'x', 'a', 'z']) expect(press(address, k, { ctrlKey: true }).defaultPrevented).toBe(false)
    // The Mac modifier does nothing here.
    expect(press(address, 't', { metaKey: true }).defaultPrevented).toBe(false)
    expect(press(address, 'w', { ctrlKey: true, altKey: true }).defaultPrevented).toBe(false)
    expect(calls('open')).toHaveLength(1)
    expect(calls('close-tab')).toHaveLength(1)
  })

  it('uses Cmd on macOS and ignores Ctrl', async () => {
    cleanup()
    setPlatform('MacIntel')
    await mount()
    push(live({ tabs: [tab(1, { agent: true }), tab(2)], activeTabId: 1 }))
    const address = screen.getByLabelText(tr['bp.address'])
    expect(press(address, 'w', { ctrlKey: true }).defaultPrevented).toBe(false)
    expect(press(address, 'w', { metaKey: true }).defaultPrevented).toBe(true)
    expect(calls('close-tab')).toEqual([{ op: 'close-tab', conversationId: 3, tabId: 1 }])
    press(address, '2', { metaKey: true })
    expect(calls('select')).toEqual([{ op: 'select', conversationId: 3, tabId: 2 }])
    expect(press(address, 'c', { metaKey: true }).defaultPrevented).toBe(false)
  })

  it('leaves Ctrl+W alone when there is no tab to close, but Ctrl+T still opens one', async () => {
    const { container } = render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    const section = container.querySelector('.bp-panel')!
    expect(press(section, 'w', { ctrlKey: true }).defaultPrevented).toBe(false)
    expect(press(section, 't', { ctrlKey: true }).defaultPrevented).toBe(true)
    await waitFor(() => expect(calls('open')).toEqual([{ op: 'open', conversationId: 3 }]))
  })

  it('maps keys with the pure helper the same way on both platforms', () => {
    const k = (key: string, over: Record<string, boolean> = {}) => ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...over })
    expect(tabShortcutOf(k('t', { ctrlKey: true }), false)).toEqual({ action: 'new' })
    expect(tabShortcutOf(k('t', { ctrlKey: true }), true)).toBeNull()
    expect(tabShortcutOf(k('T', { metaKey: true, shiftKey: true }), true)).toEqual({ action: 'reopen' })
    expect(tabShortcutOf(k('w', { metaKey: true, shiftKey: true }), true)).toBeNull()
    expect(tabShortcutOf(k('5', { ctrlKey: true }), false)).toEqual({ action: 'select', index: 5 })
    expect(tabShortcutOf(k('0', { ctrlKey: true }), false)).toBeNull()
    expect(tabShortcutOf(k('t', { ctrlKey: true, metaKey: true }), false)).toBeNull()
  })

  it('decides exactly like the page-side policy in the host for every key and modifier', () => {
    const keys = ['t', 'T', 'w', 'W', '0', '1', '5', '9', 'c', 'v', 'x', 'a', 'z', 'Tab', 'Enter']
    for (const mac of [false, true]) for (const key of keys) for (const ctrl of [false, true]) for (const meta of [false, true])
      for (const shift of [false, true]) for (const alt of [false, true]) {
        const panel = tabShortcutOf({ key, ctrlKey: ctrl, metaKey: meta, shiftKey: shift, altKey: alt }, mac)
        const page = tabShortcut({ type: 'keyDown', key, control: ctrl, meta, shift, alt }, mac ? 'darwin' : 'win32')
        expect(page, `${mac ? 'mac' : 'win'} ${key} ctrl=${ctrl} meta=${meta} shift=${shift} alt=${alt}`).toEqual(panel)
      }
  })
})

describe('BrowserPanel omnibox', () => {
  it('offers searching the web first, then matching history, and Enter on a row opens it', async () => {
    entries = [item('https://docs.unity3d.com/Manual/', 'Unity Manual', 5), item('https://itch.io/', 'itch.io: indie games', 2), item('https://news.example/', 'News', 9)]
    await mount()
    push(live())
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    fireEvent.focus(address)
    await waitFor(() => expect(calls('history').length).toBeGreaterThan(0))
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.change(address, { target: { value: 'unity' } })
    const rows = await screen.findAllByRole('option')
    expect(rows.map(r => r.getAttribute('data-kind'))).toEqual(['search', 'history'])
    expect(rows[0].querySelector('.bp-suggest-t')!.textContent).toBe(tr['bp.suggestSearch'].replace('{q}', 'unity'))
    expect(rows[1].textContent).toContain('Unity Manual')
    fireEvent.keyDown(address, { key: 'ArrowDown' })
    fireEvent.keyDown(address, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(address, { key: 'Enter' })
    expect(calls('navigate')).toEqual([{ op: 'navigate', conversationId: 3, url: 'https://docs.unity3d.com/Manual/' }])
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('Enter without a highlighted row still sends what was typed, and Escape closes the list', async () => {
    entries = [item('https://itch.io/', 'itch.io', 2)]
    await mount()
    push(live())
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'itch' } })
    await screen.findAllByRole('option')
    fireEvent.keyDown(address, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'github.com' } })
    expect((await screen.findAllByRole('option'))[0].getAttribute('data-kind')).toBe('open')
    fireEvent.keyDown(address, { key: 'Enter' })
    expect(calls('navigate')).toEqual([{ op: 'navigate', conversationId: 3, url: 'https://github.com' }])
  })

  it('a click on a row navigates, and the native view steps aside while the list is open', async () => {
    entries = [item('https://itch.io/', 'itch.io', 2)]
    await mount()
    push(live())
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: true }))
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'itch' } })
    const row = (await screen.findAllByRole('option'))[1]
    expect(document.querySelector('[role="listbox"]')).toBeTruthy()
    fireEvent.mouseDown(row)
    expect(calls('navigate')).toEqual([{ op: 'navigate', conversationId: 3, url: 'https://itch.io/' }])
  })
})

describe('BrowserPanel empty tab', () => {
  it('shows the Suggested grid of the most visited sites and keeps the native view hidden', async () => {
    entries = [item('https://a.example/x', 'A one', 1, 100), item('https://a.example/y', 'A two', 4, 200), item('https://b.example/', 'B', 3, 150)]
    await mount()
    push(live({ tabs: [tab(1, { agent: true }), tab(2, { url: '', title: '' })], activeTabId: 2 }))
    expect(await screen.findByText(tr['bp.suggested'])).toBeTruthy()
    const tiles = await screen.findAllByRole('button', { name: /example/ })
    expect(tiles.map(t => t.querySelector('.bp-tile-t')!.textContent)).toEqual(['a.example', 'b.example'])
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: false }))
    fireEvent.click(tiles[0])
    expect(calls('navigate')).toEqual([{ op: 'navigate', conversationId: 3, url: 'https://a.example/y' }])
  })

  it('says so when there is no history yet, and shows the grid again once a page loads and is left', async () => {
    await mount()
    push(live({ tabs: [tab(1, { url: 'about:blank', title: '', agent: true })], activeTabId: 1 }))
    expect(await screen.findByText(tr['bp.suggestedEmpty'])).toBeTruthy()
    push(live({ tabs: [tab(1, { agent: true })], activeTabId: 1 }))
    expect(screen.queryByText(tr['bp.suggested'])).toBeNull()
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: true }))
  })

  it('the idle panel offers the same grid and opens a tab from a tile', async () => {
    entries = [item('https://itch.io/', 'itch.io', 2)]
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    const tile = await screen.findByRole('button', { name: /itch\.io/ })
    fireEvent.click(tile)
    await waitFor(() => expect(calls('open')).toEqual([{ op: 'open', conversationId: 3, url: 'https://itch.io/' }]))
  })
})

describe('BrowserPanel agent tab and user control', () => {
  it('shows the take-over badge only while the agent tab is the one in front', async () => {
    await mount()
    push(live({ userControl: true, activeTabId: 2 }))
    expect(screen.queryByText(tr['bp.userControlShort'])).toBeNull()
    push(live({ userControl: true, activeTabId: 1 }))
    expect(screen.getByText(tr['bp.userControlShort'])).toBeTruthy()
    expect(document.querySelector('.bp-omni')!.getAttribute('data-agent')).toBe('true')
  })

  it('never sends a tab id with an agent-side op: the panel only addresses tabs through user ops', async () => {
    await mount()
    push(live())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }))
    await waitFor(() => expect(calls('halt')).toEqual([{ op: 'halt', conversationId: 3 }]))
  })
})

describe('browserSuggest', () => {
  it('ranks address matches above title matches and always puts the search row first', () => {
    const history = [item('https://a.example/guide', 'unity guide', 1, 50), item('https://unity.com/', 'Home', 1, 10), item('https://x.example/', 'Other', 9, 99)]
    const rows = suggest('unity', history)
    expect(rows.map(r => r.kind)).toEqual(['search', 'history', 'history'])
    expect(rows.map(r => r.url)).toEqual(['https://www.google.com/search?q=unity', 'https://unity.com/', 'https://a.example/guide'])
    expect(suggest('  ', history)).toEqual([])
    expect(suggest('unity hub', history).filter(r => r.kind === 'history')).toEqual([])
    expect(suggest('unity', history, 2)).toHaveLength(2)
  })

  it('suggests one tile per site, weighting recent visits', () => {
    const day = 86_400_000
    const now = 400 * day
    const history = [item('https://old.example/', 'Old', 30, now - 365 * day), item('https://new.example/', 'New', 5, now - day),
      item('https://new.example/b', 'New b', 1, now - day), item('https://www.mid.example/', 'Mid', 4, now - 10 * day)]
    const sites = suggestedSites(history, 2, now)
    expect(sites.map(s => s.label)).toEqual(['new.example', 'mid.example'])
    expect(sites[0].url).toBe('https://new.example/')
  })
})

describe('BrowserTabs icons', () => {
  it('draws only an icon the host fetched (a data: image); a remote address gets the letter tile', () => {
    const icon = 'data:image/png;base64,AQID'
    const { container } = render(<BrowserTabs activeId={1} mac={false} onSelect={() => {}} onClose={() => {}} onNew={() => {}}
      tabs={[tab(1, { favicon: icon }), tab(2, { favicon: 'https://tracker.example/pixel.png' }), tab(3, { favicon: 'javascript:alert(1)' })]} />)
    expect(Array.from(container.querySelectorAll('img')).map(img => img.getAttribute('src'))).toEqual([icon])
  })
})
