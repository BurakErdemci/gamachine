import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

const ipc = vi.hoisted(() => {
  const api = { invoke: vi.fn(async () => ({ success: true })), on: vi.fn(() => () => {}) }
  window.ipc = api as any
  return api
})
const terms = vi.hoisted(() => [] as any[])
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  options: any
  cols = 80; rows = 24
  selection = 'terminal output'
  selected = true
  selectionChanged = () => {}
  disposeSelection = vi.fn()
  constructor(options: any) { this.options = options; terms.push(this) }
  loadAddon() {} open() {} write() {} focus() {} clear() {} dispose() {}
  onData() { return { dispose() {} } }
  onSelectionChange(callback: () => void) {
    this.selectionChanged = callback
    return { dispose: this.disposeSelection }
  }
  hasSelection() { return this.selected }
  getSelection() { return this.selection }
} }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

import { COPIED_EVENT, announceCopied, selectedTextToCopy, installCopyOnSelect } from '../renderer/lib/copyOnSelect'
import { CopyOnSelect } from '../renderer/components/ui/CopyOnSelect'
import { TerminalPanel } from '../renderer/components/home/TerminalPanel'
import { AppearancePage } from '../renderer/components/home/settings/SettingsPages'
import { AppearanceProvider, loadAppearance, saveAppearance, DEFAULTS, useAppearance } from '../renderer/lib/appearance'
import { LangContext, ceviriUygula, translations, type Lang } from '../renderer/lib/i18n'

const writeText = vi.fn().mockResolvedValue(undefined)
let removeListener: (() => void) | undefined

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  terms.length = 0
  ipc.invoke.mockClear()
  writeText.mockReset().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText } })
})
afterEach(() => {
  removeListener?.()
  removeListener = undefined
  cleanup()
  document.getSelection()?.removeAllRanges()
  localStorage.clear()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function selectText(start: Node, end = start) {
  const range = document.createRange()
  range.setStart(start, 0)
  range.setEnd(end, end.textContent?.length ?? 0)
  const selection = document.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  return selection
}

function releaseMouse(target: Document | Element = document, clientX = 100, clientY = 200) {
  fireEvent.mouseUp(target, { clientX, clientY })
  act(() => { vi.runOnlyPendingTimers() })
}

function Harness({ terminal = false }) {
  const { appearance, setAppearance } = useAppearance()
  return <>
    <button onClick={() => setAppearance({ copyOnSelect: !appearance.copyOnSelect })}>toggle copy</button>
    <CopyOnSelect />
    <div data-testid="plain"> selected text </div>
    {terminal && <TerminalPanel id="copy-test" isOpen onClose={() => {}} workspacePath={null} />}
  </>
}

describe('document copy on select', () => {
  it('copies the settled selection on mouse release, preserving its text', () => {
    render(<AppearanceProvider><Harness /></AppearanceProvider>)
    fireEvent.mouseUp(document)
    expect(writeText).not.toHaveBeenCalled()
    selectText(screen.getByTestId('plain').firstChild!)
    act(() => { vi.runOnlyPendingTimers() })
    expect(writeText).toHaveBeenCalledExactlyOnceWith(' selected text ')
  })

  it('ignores null, collapsed and whitespace-only selections', () => {
    expect(selectedTextToCopy(null, null)).toBeNull()
    render(<div data-testid="blank">{' \n\t '}</div>)
    const selection = selectText(screen.getByTestId('blank').firstChild!)
    expect(selectedTextToCopy(selection, null)).toBeNull()
    selection.collapseToStart()
    expect(selectedTextToCopy(selection, null)).toBeNull()
    removeListener = installCopyOnSelect(() => true)
    releaseMouse()
    expect(writeText).not.toHaveBeenCalled()
  })

  it.each(['input', 'textarea', 'contenteditable', 'monaco'])('ignores active editing in %s', kind => {
    const { container } = render(<>
      <div data-testid="plain">text</div>
      {kind === 'input' ? <input /> : kind === 'textarea' ? <textarea /> :
        <div className={kind === 'monaco' ? 'monaco-editor' : undefined}
          contentEditable={kind === 'contenteditable' ? true : undefined} suppressContentEditableWarning>
          <span tabIndex={0}>editing</span>
        </div>}
    </>)
    const editor = container.querySelector('input, textarea, span') as HTMLElement
    editor.focus()
    selectText(screen.getByTestId('plain').firstChild!)
    removeListener = installCopyOnSelect(() => true)
    releaseMouse()
    expect(writeText).not.toHaveBeenCalled()
  })

  it.each(['textarea', 'contenteditable', 'monaco'])('ignores either selection endpoint inside %s', kind => {
    render(<>
      <div data-testid="before">before</div>
      {kind === 'textarea' ? <textarea data-testid="editor" defaultValue="editing" /> :
        <div className={kind === 'monaco' ? 'monaco-editor' : undefined}
          contentEditable={kind === 'contenteditable' ? true : undefined} suppressContentEditableWarning>
          <span data-testid="editor">editing</span>
        </div>}
      <div data-testid="after">after</div>
    </>)
    removeListener = installCopyOnSelect(() => true)
    selectText(screen.getByTestId('before').firstChild!, screen.getByTestId('editor').firstChild!)
    releaseMouse()
    selectText(screen.getByTestId('editor').firstChild!, screen.getByTestId('after').firstChild!)
    releaseMouse()
    expect(writeText).not.toHaveBeenCalled()
  })

  it('rejects an input endpoint even when no input is active', () => {
    render(<input data-testid="editor" />)
    const selection = {
      isCollapsed: false, anchorNode: screen.getByTestId('editor'), focusNode: document.body,
      toString: () => 'editing',
    } as unknown as Selection
    expect(selectedTextToCopy(selection, null)).toBeNull()
  })

  it('allows contenteditable=false text', () => {
    render(<div contentEditable={false}><span data-testid="text">read only</span></div>)
    selectText(screen.getByTestId('text').firstChild!)
    removeListener = installCopyOnSelect(() => true)
    releaseMouse()
    expect(writeText).toHaveBeenCalledWith('read only')
  })

  it('responds to toggles without reinstalling and cancels pending work on unmount', () => {
    const add = vi.spyOn(document, 'addEventListener')
    const view = render(<AppearanceProvider><Harness /></AppearanceProvider>)
    const installs = () => add.mock.calls.filter(([event]) => event === 'mouseup').length
    expect(installs()).toBe(1)
    selectText(screen.getByTestId('plain').firstChild!)
    fireEvent.click(screen.getByText('toggle copy'))
    releaseMouse()
    expect(writeText).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('toggle copy'))
    releaseMouse()
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(installs()).toBe(1)
    fireEvent.mouseUp(document)
    view.unmount()
    releaseMouse()
    expect(writeText).toHaveBeenCalledTimes(1)
  })

  it('does not copy when disabled between release and the deferred read', () => {
    render(<AppearanceProvider><Harness /></AppearanceProvider>)
    selectText(screen.getByTestId('plain').firstChild!)
    fireEvent.mouseUp(document)
    fireEvent.click(screen.getByText('toggle copy'))
    act(() => { vi.runOnlyPendingTimers() })
    expect(writeText).not.toHaveBeenCalled()
  })

  it('silently handles clipboard rejection, synchronous failure and missing clipboard', async () => {
    render(<div data-testid="text">copy</div>)
    selectText(screen.getByTestId('text').firstChild!)
    removeListener = installCopyOnSelect(() => true)
    writeText.mockRejectedValueOnce(new Error('denied'))
    releaseMouse()
    await act(async () => { await Promise.resolve() })
    writeText.mockImplementationOnce(() => { throw new Error('unavailable') })
    expect(releaseMouse).not.toThrow()
    vi.stubGlobal('navigator', {})
    expect(releaseMouse).not.toThrow()
  })
})

describe('terminal copy on select', () => {
  it('copies xterm selection, reads toggles without recreating the terminal and disposes the listener', () => {
    const view = render(<AppearanceProvider><Harness terminal /></AppearanceProvider>)
    const host = view.container.querySelector('.term-host')!
    expect(terms).toHaveLength(1)
    const term = terms[0]
    term.selectionChanged()
    expect(writeText).not.toHaveBeenCalled()
    releaseMouse(host)
    expect(writeText).toHaveBeenCalledExactlyOnceWith('terminal output')
    fireEvent.click(screen.getByText('toggle copy'))
    term.selectionChanged()
    releaseMouse(host)
    expect(writeText).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('toggle copy'))
    term.selectionChanged()
    releaseMouse(host)
    expect(writeText).toHaveBeenCalledTimes(2)
    expect(terms).toHaveLength(1)
    term.selection = ' \n '
    term.selectionChanged()
    releaseMouse(host)
    term.selection = 'text'
    term.selected = false
    term.selectionChanged()
    releaseMouse(host)
    expect(writeText).toHaveBeenCalledTimes(2)
    view.unmount()
    releaseMouse(host)
    expect(writeText).toHaveBeenCalledTimes(2)
  })

  it('silently handles clipboard failures and missing clipboard', async () => {
    const view = render(<AppearanceProvider><Harness terminal /></AppearanceProvider>)
    const host = view.container.querySelector('.term-host')!
    writeText.mockRejectedValueOnce(new Error('denied'))
    releaseMouse(host)
    await act(async () => { await Promise.resolve() })
    expect(view.container.querySelector('.gm-copied')).toBeNull()
    writeText.mockImplementationOnce(() => { throw new Error('unavailable') })
    expect(() => releaseMouse(host)).not.toThrow()
    vi.stubGlobal('navigator', {})
    expect(() => releaseMouse(host)).not.toThrow()
    await act(async () => { await Promise.resolve() })
    expect(view.container.querySelector('.gm-copied')).toBeNull()
  })

  it('announces once at release after a successful write, never while dragging', async () => {
    let finish!: () => void
    writeText.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve }))
    const announced = vi.fn()
    window.addEventListener(COPIED_EVENT, announced)
    try {
      const view = render(<AppearanceProvider><Harness terminal /></AppearanceProvider>)
      const host = view.container.querySelector('.term-host')!
      terms[0].selectionChanged()
      terms[0].selectionChanged()
      expect(writeText).not.toHaveBeenCalled()
      expect(announced).not.toHaveBeenCalled()
      releaseMouse(host, 40, 60)
      expect(writeText).toHaveBeenCalledTimes(1)
      expect(announced).not.toHaveBeenCalled()
      await act(async () => { finish() })
      expect(announced).toHaveBeenCalledTimes(1)
      expect(announced.mock.calls[0][0].detail).toEqual({ x: 40, y: 60 })
      expect(screen.getByRole('status').style.left).toBe('52px')
      expect(screen.getByRole('status').style.top).toBe('72px')
    } finally {
      window.removeEventListener(COPIED_EVENT, announced)
    }
  })
})

describe('copied chip', () => {
  it('appears after the write resolves at the release position and expires at 1200 ms', async () => {
    let finish!: () => void
    writeText.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve }))
    render(<AppearanceProvider><Harness /></AppearanceProvider>)
    selectText(screen.getByTestId('plain').firstChild!)
    releaseMouse(document, 25, 35)
    expect(screen.queryByRole('status')).toBeNull()
    await act(async () => { finish() })
    const chip = screen.getByRole('status')
    expect(chip.textContent).toBe('Kopyalandı')
    expect(chip.className).toBe('gm-copied')
    expect(chip.getAttribute('aria-live')).toBe('polite')
    expect(chip.style.left).toBe('37px')
    expect(chip.style.top).toBe('47px')
    act(() => { vi.advanceTimersByTime(1199) })
    expect(screen.getByRole('status')).toBe(chip)
    act(() => { vi.advanceTimersByTime(1) })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('replaces the chip and restarts its lifetime for a second copy', () => {
    render(<AppearanceProvider><Harness /></AppearanceProvider>)
    act(() => { announceCopied(10, 20) })
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => { announceCopied(30, 40) })
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status').style.left).toBe('42px')
    act(() => { vi.advanceTimersByTime(1199) })
    expect(screen.getAllByRole('status')).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(1) })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('clamps the measured chip to an eight-pixel viewport margin', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(90)
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(28)
    render(<AppearanceProvider><Harness /></AppearanceProvider>)
    act(() => { announceCopied(window.innerWidth, window.innerHeight) })
    expect(screen.getByRole('status').style.left).toBe(`${window.innerWidth - 98}px`)
    expect(screen.getByRole('status').style.top).toBe(`${window.innerHeight - 36}px`)
    act(() => { announceCopied(-20, -20) })
    expect(screen.getByRole('status').style.left).toBe('8px')
    expect(screen.getByRole('status').style.top).toBe('8px')
  })

  it.each(['rejected', 'throws', 'missing', 'blank', 'editable', 'disabled'])('stays hidden for %s copies', async kind => {
    render(<AppearanceProvider><Harness /><div data-testid="blank">{'   '}</div><input /></AppearanceProvider>)
    selectText(screen.getByTestId(kind === 'blank' ? 'blank' : 'plain').firstChild!)
    if (kind === 'rejected') writeText.mockRejectedValueOnce(new Error('denied'))
    if (kind === 'throws') writeText.mockImplementationOnce(() => { throw new Error('unavailable') })
    if (kind === 'missing') vi.stubGlobal('navigator', {})
    if (kind === 'editable') screen.getByRole('textbox').focus()
    if (kind === 'disabled') fireEvent.click(screen.getByText('toggle copy'))
    releaseMouse()
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByRole('status')).toBeNull()
    if (['blank', 'editable', 'disabled'].includes(kind)) expect(writeText).not.toHaveBeenCalled()
  })

  it('uses the English translation and removes its event listener on unmount', () => {
    const view = render(<LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (key, values) => ceviriUygula('en', key, values) }}>
      <AppearanceProvider><Harness /></AppearanceProvider>
    </LangContext.Provider>)
    act(() => { announceCopied(10, 20) })
    expect(screen.getByRole('status').textContent).toBe('Copied')
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
    act(() => { announceCopied(10, 20) })
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('copy-on-select preference', () => {
  it.each([null, 'on', 'invalid', 'off'])('loads stored value %s', value => {
    if (value !== null) localStorage.setItem('app-copy-on-select', value)
    expect(loadAppearance().copyOnSelect).toBe(value !== 'off')
    saveAppearance({ ...DEFAULTS, copyOnSelect: value !== 'off' })
    expect(localStorage.getItem('app-copy-on-select')).toBe(value === 'off' ? 'off' : 'on')
  })

  it.each(['en', 'tr'] as const)('persists the settings switch in %s', (lang: Lang) => {
    const saved = vi.fn()
    render(<LangContext.Provider value={{ lang, setLang: () => {}, t: (key, values) => ceviriUygula(lang, key, values) }}>
      <AppearanceProvider><AppearancePage saved={saved} /></AppearanceProvider>
    </LangContext.Provider>)
    const toggle = screen.getByRole('switch', { name: translations[lang]['settings.appearance.copyOnSelect'] })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(localStorage.getItem('app-copy-on-select')).toBe('off')
    expect(saved).toHaveBeenCalledTimes(1)
    expect(loadAppearance().copyOnSelect).toBe(false)
  })
})
