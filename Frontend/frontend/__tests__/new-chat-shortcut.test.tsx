/**
 * Ctrl+N creates a new chat only when nothing else owns the key.
 *
 * Audit of P1: the window listener had no guards. In the xterm terminal Ctrl+N is readline's
 * next-history and each press POSTed a new conversation; holding the key auto-repeated; a
 * rename input or an open modal did not stop it. The hook is what home.tsx mounts.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, cleanup } from '@testing-library/react'
import { useNewChatShortcut } from '../renderer/lib/newChatShortcut'

afterEach(() => { cleanup(); document.body.innerHTML = '' })

function press(target: EventTarget, init: KeyboardEventInit = {}) {
  const e = new KeyboardEvent('keydown', { key: 'n', ctrlKey: true, bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(e)
  return e
}

function mount() {
  const onNew = vi.fn()
  renderHook(() => useNewChatShortcut(onNew))
  return onNew
}

describe('useNewChatShortcut', () => {
  it('creates a chat on a plain Ctrl+N from the page', () => {
    const onNew = mount()
    const e = press(document.body)
    expect(onNew).toHaveBeenCalledTimes(1)
    expect(e.defaultPrevented).toBe(true)
  })

  it('ignores Ctrl+N typed in the terminal (readline next-history)', () => {
    const onNew = mount()
    const term = document.createElement('div')
    term.className = 'xterm'
    const helper = document.createElement('textarea')
    helper.className = 'xterm-helper-textarea'
    term.appendChild(helper)
    document.body.appendChild(term)
    const e = press(helper)
    expect(onNew).not.toHaveBeenCalled()
    // The terminal still receives its own key.
    expect(e.defaultPrevented).toBe(false)
  })

  it('ignores the auto-repeat of a held key', () => {
    const onNew = mount()
    press(document.body, { repeat: true })
    press(document.body, { repeat: true })
    expect(onNew).not.toHaveBeenCalled()
  })

  it('ignores Ctrl+N while an input has focus (sidebar rename, Monaco textarea)', () => {
    const onNew = mount()
    const input = document.createElement('input')
    document.body.appendChild(input)
    press(input)
    const ta = document.createElement('textarea')
    document.body.appendChild(ta)
    press(ta)
    const editable = document.createElement('div')
    editable.setAttribute('contenteditable', 'true')
    document.body.appendChild(editable)
    press(editable)
    expect(onNew).not.toHaveBeenCalled()
  })

  it('ignores Ctrl+N while a modal is open', () => {
    const onNew = mount()
    const modal = document.createElement('div')
    modal.setAttribute('role', 'alertdialog')
    modal.setAttribute('aria-modal', 'true')
    document.body.appendChild(modal)
    press(document.body)
    expect(onNew).not.toHaveBeenCalled()
  })

  it('ignores an event another handler already claimed', () => {
    const onNew = mount()
    const el = document.createElement('div')
    document.body.appendChild(el)
    el.addEventListener('keydown', ev => ev.preventDefault())
    press(el)
    expect(onNew).not.toHaveBeenCalled()
  })
})
