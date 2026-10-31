import React, { useState } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConfirmDialogHost, confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { translations } from '../renderer/lib/i18n'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { isBrowserFullShortcut, useBrowserFull } from '../renderer/lib/browserFull'

const tr = translations.tr

let listeners: Map<string, (payload: unknown) => void>
let invoke: ReturnType<typeof vi.fn>
let rect: { left: number; top: number; width: number; height: number }

const live = () => ({
  active: true, conversationId: 3, url: 'https://itch.io/game-assets', title: 'Free assets', loading: false,
  tabs: [{ id: 1, url: 'https://itch.io/game-assets', title: 'Free assets', loading: false, favicon: '', agent: true }], activeTabId: 1,
})
const bounds = () => invoke.mock.calls.filter(c => c[1]?.op === 'set-bounds').map(c => c[1].bounds)
/** What the hook told main for its Esc rule in the native views (browser-host.ts escape()). */
const fullReports = () => invoke.mock.calls.filter(c => c[1]?.op === 'full').map(c => ({ on: c[1].on }))
/** Esc in this window that no control used, handed to main's rule (op 'escape'). */
const escapes = () => invoke.mock.calls.filter(c => c[1]?.op === 'escape').length
/** The hook reads whether the key was used once every listener has run (a 0 ms timer). */
const flush = () => act(() => new Promise(r => setTimeout(r, 0)))

let mounts: { chat: number; panel: number }
function Chat() {
  const [text, setText] = useState('')
  React.useEffect(() => { mounts.chat += 1 }, [])
  return <textarea aria-label="composer" value={text} onChange={e => setText(e.target.value)} />
}
function Panel(props: React.ComponentProps<typeof BrowserPanel>) {
  React.useEffect(() => { mounts.panel += 1 }, [])
  return <BrowserPanel {...props} />
}

/** home.tsx's wiring in miniature: the hook, the attribute on .app, the panel kept mounted. */
function Frame({ browserShown }: { browserShown: boolean }) {
  const bf = useBrowserFull(browserShown)
  return (
    <div className="app" data-testid="app" data-browser-full={bf.full ? 'true' : undefined}
      data-bf-chat={bf.full && !bf.chatOpen ? 'closed' : undefined}>
      <aside className="sidebar-slot" />
      <Panel visible={browserShown} conversationId={3} running={false} onStop={async () => {}} onNeedChat={async () => 3}
        full={bf.full} onToggleFull={bf.toggle} chatOpen={bf.chatOpen} onToggleChat={bf.toggleChat} />
      <Chat />
    </div>
  )
}

beforeEach(() => {
  mounts = { chat: 0, panel: 0 }
  listeners = new Map()
  rect = { left: 10, top: 20, width: 300, height: 200 }
  invoke = vi.fn(async (_c: string, req: any) => (req?.op === 'panel-status' ? live() : { ok: true }))
  vi.stubGlobal('ipc', {
    invoke,
    on: vi.fn((channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) }),
  })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const r = this.classList.contains('bp-view') ? rect : { left: 0, top: 0, width: 0, height: 0 }
    return { ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top, toJSON() {} } as DOMRect
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const app = () => screen.getByTestId('app')
const platform = (value: string) => vi.spyOn(navigator, 'platform', 'get').mockReturnValue(value)
const toggleBtn = () => screen.getByRole('button', { name: /Tam ekran|Full screen|Leave full screen/ })
const key = (init: KeyboardEventInit) => fireEvent.keyDown(window, init)

describe('browser full screen', () => {
  it('turns on from the toolbar toggle and off from the same toggle', async () => {
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    expect(app().getAttribute('data-browser-full')).toBeNull()
    fireEvent.click(toggleBtn())
    expect(app().getAttribute('data-browser-full')).toBe('true')
    expect(toggleBtn().getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(toggleBtn())
    expect(app().getAttribute('data-browser-full')).toBeNull()
  })

  it('names the shortcut for the platform in the tooltip, in both languages', async () => {
    platform('Win32')
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    expect(toggleBtn().getAttribute('title')).toBe(tr['bp.fullEnter'].replace('{kisayol}', 'Ctrl+Shift+F'))
    expect(translations.en['bp.fullEnter']).toContain('{kisayol}')
    expect(translations.en['bp.fullLeave']).toContain('{kisayol}')
  })

  it('uses Ctrl+Shift+F on Windows and Cmd+Shift+F on macOS', () => {
    const f = (over: Partial<KeyboardEvent>) => ({ key: 'F', ctrlKey: false, metaKey: false, shiftKey: true, altKey: false, ...over })
    expect(isBrowserFullShortcut(f({ ctrlKey: true }), false)).toBe(true)
    expect(isBrowserFullShortcut(f({ metaKey: true }), false)).toBe(false)
    expect(isBrowserFullShortcut(f({ metaKey: true }), true)).toBe(true)
    expect(isBrowserFullShortcut(f({ ctrlKey: true }), true)).toBe(false)
    expect(isBrowserFullShortcut(f({ ctrlKey: true, shiftKey: false }), false)).toBe(false)
    expect(isBrowserFullShortcut(f({ ctrlKey: true, altKey: true }), false)).toBe(false)
  })

  it('toggles from the keyboard shortcut of the platform', async () => {
    platform('Win32')
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    key({ key: 'F', ctrlKey: true, shiftKey: true })
    expect(app().getAttribute('data-browser-full')).toBe('true')
    key({ key: 'F', ctrlKey: true, shiftKey: true })
    expect(app().getAttribute('data-browser-full')).toBeNull()
    key({ key: 'F', metaKey: true, shiftKey: true })
    expect(app().getAttribute('data-browser-full')).toBeNull()
  })

  it('uses Cmd+Shift+F on a Mac', async () => {
    platform('MacIntel')
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    key({ key: 'f', metaKey: true, shiftKey: true })
    expect(app().getAttribute('data-browser-full')).toBe('true')
  })

  it('tells main when full screen goes on and off, and an Esc that drops an address edit stays the box\'s', async () => {
    render(<Frame browserShown />)
    const address = await screen.findByRole('combobox')
    fireEvent.click(toggleBtn())
    await waitFor(() => expect(fullReports()).toEqual([{ on: true }]))
    act(() => { address.focus() })
    fireEvent.keyDown(address, { key: 'Escape' })
    await flush()
    expect(escapes()).toBe(0)
    fireEvent.click(toggleBtn())
    await waitFor(() => expect(fullReports()).toEqual([{ on: true }, { on: false }]))
  })

  it('leaves when main ends full screen on Esc, and only then', async () => {
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    fireEvent.click(toggleBtn())
    // An unused Esc goes to main's rule; the mode ends when main says so.
    key({ key: 'Escape' })
    await flush()
    expect(escapes()).toBe(1)
    expect(app().getAttribute('data-browser-full')).toBe('true')
    act(() => { listeners.get('browser-full-toggle')?.({ on: false }) })
    expect(app().getAttribute('data-browser-full')).toBeNull()
    act(() => { listeners.get('browser-full-toggle')?.({ on: false }) })
    expect(app().getAttribute('data-browser-full')).toBeNull()
  })

  it('does nothing while the browser is not shown, and leaves when it is hidden', async () => {
    const { rerender } = render(<Frame browserShown={false} />)
    key({ key: 'F', ctrlKey: true, shiftKey: true })
    expect(app().getAttribute('data-browser-full')).toBeNull()
    rerender(<Frame browserShown />)
    await screen.findByRole('combobox')
    fireEvent.click(toggleBtn())
    expect(app().getAttribute('data-browser-full')).toBe('true')
    rerender(<Frame browserShown={false} />)
    expect(app().getAttribute('data-browser-full')).toBeNull()
    rerender(<Frame browserShown />)
    expect(app().getAttribute('data-browser-full')).toBeNull()
  })

  it('keeps the chat and the panel mounted with their state across enter and leave', async () => {
    render(<Frame browserShown />)
    const address = await screen.findByRole('combobox')
    fireEvent.change(screen.getByLabelText('composer'), { target: { value: 'what is on this page?' } })
    fireEvent.click(toggleBtn())
    fireEvent.click(toggleBtn())
    expect((screen.getByLabelText('composer') as HTMLTextAreaElement).value).toBe('what is on this page?')
    expect(screen.getByRole('combobox')).toBe(address)
    expect(mounts).toEqual({ chat: 1, panel: 1 })
  })

  it('sends the new bounds to the native view after the transition', async () => {
    render(<Frame browserShown />)
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 10, y: 20, width: 300, height: 200 }))
    rect = { left: 0, top: 52, width: 1200, height: 560 }
    fireEvent.click(toggleBtn())
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 0, y: 52, width: 1200, height: 560 }))
    rect = { left: 10, top: 20, width: 300, height: 200 }
    fireEvent.click(toggleBtn())
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 10, y: 20, width: 300, height: 200 }))
  })

  it('collapses the floating chat to a toolbar button and back, sending the page bounds each time', async () => {
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    expect(screen.queryByRole('button', { name: tr['bf.chatHide'] })).toBeNull()
    rect = { left: 0, top: 52, width: 800, height: 700 }
    fireEvent.click(toggleBtn())
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 0, y: 52, width: 800, height: 700 }))
    const hide = screen.getByRole('button', { name: tr['bf.chatHide'] })
    expect(hide.getAttribute('aria-pressed')).toBe('true')
    rect = { left: 0, top: 52, width: 1240, height: 700 }
    fireEvent.click(hide)
    expect(app().getAttribute('data-bf-chat')).toBe('closed')
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 0, y: 52, width: 1240, height: 700 }))
    rect = { left: 0, top: 52, width: 800, height: 700 }
    fireEvent.click(screen.getByRole('button', { name: tr['bf.chatShow'] }))
    expect(app().getAttribute('data-bf-chat')).toBeNull()
    await waitFor(() => expect(bounds().at(-1)).toEqual({ x: 0, y: 52, width: 800, height: 700 }))
    // Leaving full screen takes the button away; the normal layout carries no chat attribute.
    fireEvent.click(toggleBtn())
    expect(screen.queryByRole('button', { name: tr['bf.chatHide'] })).toBeNull()
    expect(app().getAttribute('data-browser-full')).toBeNull()
  })

  it('shows the toggle in the empty browser too, so Esc is not the only way out', async () => {
    invoke.mockImplementation(async (_c: string, req: any) => (req?.op === 'panel-status' ? { active: false } : { ok: true }))
    render(<Frame browserShown />)
    await screen.findByText(tr['bp.idle'])
    fireEvent.click(toggleBtn())
    expect(app().getAttribute('data-browser-full')).toBe('true')
  })
})

describe('browser full screen keyboard rules', () => {
  const full = () => app().getAttribute('data-browser-full') === 'true'
  const enter = async () => {
    await screen.findByRole('combobox')
    fireEvent.click(toggleBtn())
    expect(full()).toBe(true)
  }

  it('keeps full mode when Esc closes a real confirm dialog', async () => {
    render(<Frame browserShown />)
    await enter()
    render(<ConfirmDialogHost />)
    let answer: boolean | undefined
    await act(async () => { void confirmDialog('delete?').then(v => { answer = v }) })
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    await act(async () => { key({ key: 'Escape' }) })
    await flush()
    expect(answer).toBe(false)
    expect(full()).toBe(true)
    expect(escapes()).toBe(0)
  })

  it('keeps full mode when Esc closes a document-level popup with focus in the composer', async () => {
    render(<Frame browserShown />)
    await enter()
    let open = true
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); open = false } }
    document.addEventListener('keydown', onKey)
    fireEvent.keyDown(screen.getByLabelText('composer'), { key: 'Escape' })
    document.removeEventListener('keydown', onKey)
    await flush()
    expect(open).toBe(false)
    expect(full()).toBe(true)
    expect(escapes()).toBe(0)
  })

  it('hands an Esc nothing used in the composer to main, and never a modified, repeated or composing one', async () => {
    render(<Frame browserShown />)
    await enter()
    const composer = screen.getByLabelText('composer')
    for (const init of [{ shiftKey: true }, { ctrlKey: true }, { altKey: true }, { metaKey: true }, { repeat: true }, { isComposing: true }]) {
      fireEvent.keyDown(composer, { key: 'Escape', ...init })
    }
    await flush()
    expect(escapes()).toBe(0)
    fireEvent.keyDown(composer, { key: 'Escape' })
    await flush()
    expect(escapes()).toBe(1)
  })

  it('ignores the chord while a modal dialog is open and on key auto-repeat', async () => {
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    const modal = document.createElement('div')
    modal.setAttribute('aria-modal', 'true')
    document.body.appendChild(modal)
    key({ key: 'F', ctrlKey: true, shiftKey: true })
    expect(full()).toBe(false)
    modal.remove()
    for (let i = 0; i < 3; i++) key({ key: 'F', ctrlKey: true, shiftKey: true, repeat: i > 0 })
    expect(full()).toBe(true)
  })

  it('toggles when main forwards the chord pressed inside the native page', async () => {
    render(<Frame browserShown />)
    await screen.findByRole('combobox')
    act(() => { listeners.get('browser-full-toggle')?.(undefined) })
    expect(full()).toBe(true)
    act(() => { listeners.get('browser-full-toggle')?.(undefined) })
    expect(full()).toBe(false)
  })

  it('is not shown while the settings, profile or guide screen is up', async () => {
    function Screened({ screen }: { screen: boolean }) {
      const bf = useBrowserFull(true && !screen)
      return <div data-testid="s" data-full={bf.full ? 'y' : 'n'}><button onClick={bf.toggle}>t</button></div>
    }
    const { rerender } = render(<Screened screen={false} />)
    fireEvent.click(screen.getByText('t'))
    expect(screen.getByTestId('s').dataset.full).toBe('y')
    rerender(<Screened screen />)
    expect(screen.getByTestId('s').dataset.full).toBe('n')
  })

  it('leaves when the conversation changes', async () => {
    function ByChat({ chat }: { chat: number }) {
      const bf = useBrowserFull(true, chat)
      return <div data-testid="c" data-full={bf.full ? 'y' : 'n'}><button onClick={bf.toggle}>t</button></div>
    }
    const hooked = render(<ByChat chat={3} />)
    fireEvent.click(screen.getByText('t'))
    expect(screen.getByTestId('c').dataset.full).toBe('y')
    hooked.rerender(<ByChat chat={4} />)
    expect(screen.getByTestId('c').dataset.full).toBe('n')
    hooked.unmount()
  })

  it('keeps full mode when Esc closes the chat image lightbox (its handler uses the key)', () => {
    const src = readFileSync(join(__dirname, '../renderer/components/home/ChatPanel.tsx'), 'utf8')
    expect(src).toMatch(/\{lightboxSrc && \(\s*<div\s+role="dialog" aria-modal="true"/)
    expect(src).toContain("if (e.key === 'Escape') { e.preventDefault(); setLightboxSrc(null); }")
  })

  it('names Esc in the leave tooltip, now that it works wherever focus is', () => {
    for (const lang of ['tr', 'en'] as const) {
      const text = translations[lang]['bp.fullLeave']
      expect(text).toContain('{kisayol}')
      expect(text).toMatch(/(ya da|or) Esc/)
      expect(text).not.toMatch(lang === 'tr' ? /sayfanın dışındayken/ : /outside the page/)
    }
  })

  it('home.tsx passes the screen rule into the hook', () => {
    const src = readFileSync(join(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(src).toMatch(/useBrowserFull\([^)]*!ai\.showSettings && !profileOpen && !guide\.guideOpen, chat\.activeConvId,\s*cardWaiting/)
  })
})

describe('browser full screen styles', () => {
  const css = (name: string) => readFileSync(join(__dirname, '../renderer/styles/gm', name), 'utf8')
  const rule = (name: string, selector: string) => {
    const text = css(name)
    const at = text.indexOf(selector)
    return at < 0 ? '' : text.slice(at, text.indexOf('}', at) + 1)
  }

  it('lays the page over the whole area, the chat floating on the right in a gutter the page view leaves', () => {
    expect(css('shell.css')).toContain('--bf-chat-w: clamp(300px, 30vw, 420px); --bf-gap: 10px;')
    expect(rule('shell.css', '.app[data-browser-full]:not([data-screen]) .app-body {')).toContain('grid-template-rows: minmax(0, 1fr)')
    const chat = rule('shell.css', '.app[data-browser-full]:not([data-screen]) .app-body > .app-main {')
    expect(chat).toContain('position: absolute')
    expect(chat).toContain('right: var(--bf-gap)')
    expect(chat).toContain('width: var(--bf-chat-w)')
    // The page view's panel keeps exactly the chat's width and both margins free; collapsed, it takes the whole width.
    expect(rule('shell.css', '.app[data-browser-full]:not([data-screen]):not([data-bf-chat="closed"]) .bp-panel[data-full="true"] {'))
      .toContain('padding-right: calc(var(--bf-chat-w) + 2 * var(--bf-gap))')
    expect(rule('shell.css', '.app[data-browser-full][data-bf-chat="closed"]:not([data-screen]) .app-body > .app-main {')).toContain('visibility: hidden')
    expect(css('shell.css')).toMatch(/\[data-browser-full\]:not\(\[data-screen\]\) > \.sidebar-slot \{ display: none; \}/)
    expect(css('shell.css')).toMatch(/\.workspace :is\(\.ws-head, \.ws-tabs, \.live-tabs\) \{ display: none; \}/)
  })

  it.each([['sade', 'theme-sade.shell.css'], ['pafta', 'theme-pafta.shell.css'], ['atolye', 'theme-atolye.shell.css'],
    ['ozel', 'theme-ozel.shell.css'], ['arena', 'theme-arena.shell.css']])('%s styles the floating chat', (theme, file) => {
    const text = css(file)
    const selector = `[data-theme="${theme}"] .app[data-browser-full]:not([data-screen]) .app-body > .app-main`
    expect(text).toContain(selector)
    expect(rule(file, selector)).toMatch(/border|box-shadow|clip-path/)
  })

  it('adds no colour literal of its own', () => {
    const own = ['shell.css', 'thread.css', 'browser-panel.css', 'theme-sade.shell.css', 'theme-pafta.shell.css', 'theme-atolye.shell.css', 'theme-ozel.shell.css', 'theme-arena.shell.css']
      .flatMap(f => css(f).split('\n').filter(l => /data-browser-full|\.bp-full|\.bp-panel\[data-full/.test(l)))
    expect(own.length).toBeGreaterThan(8)
    expect(own.join('\n')).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/)
  })
})
