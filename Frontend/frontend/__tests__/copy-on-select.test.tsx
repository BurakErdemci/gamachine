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

import { selectedTextToCopy, installCopyOnSelect } from '../renderer/lib/copyOnSelect'
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

function releaseMouse() {
  fireEvent.mouseUp(document)
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
    expect(terms).toHaveLength(1)
    const term = terms[0]
    term.selectionChanged()
    expect(writeText).toHaveBeenCalledExactlyOnceWith('terminal output')
    fireEvent.click(screen.getByText('toggle copy'))
    term.selectionChanged()
    expect(writeText).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('toggle copy'))
    term.selectionChanged()
    expect(writeText).toHaveBeenCalledTimes(2)
    expect(terms).toHaveLength(1)
    term.selection = ' \n '
    term.selectionChanged()
    term.selection = 'text'
    term.selected = false
    term.selectionChanged()
    expect(writeText).toHaveBeenCalledTimes(2)
    view.unmount()
    expect(term.disposeSelection).toHaveBeenCalledTimes(1)
  })

  it('silently handles clipboard failures and missing clipboard', async () => {
    render(<AppearanceProvider><Harness terminal /></AppearanceProvider>)
    writeText.mockRejectedValueOnce(new Error('denied'))
    terms[0].selectionChanged()
    await act(async () => { await Promise.resolve() })
    writeText.mockImplementationOnce(() => { throw new Error('unavailable') })
    expect(() => terms[0].selectionChanged()).not.toThrow()
    vi.stubGlobal('navigator', {})
    expect(() => terms[0].selectionChanged()).not.toThrow()
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
