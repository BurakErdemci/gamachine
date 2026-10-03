import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { isMacPlatform, platformKeys } from '../renderer/lib/platformKeys'
import { tx } from '../renderer/lib/guide/registry'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const noop = () => {}
const sidebarProps: React.ComponentProps<typeof Sidebar> = {
  isSidebarOpen: true, conversations: [], activeConvId: null,
  selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
  editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
  workspacePath: null, closeWorkspace: noop,
  user: { id: 1, name: 'Burak', sessionToken: 't' }, setShowSettings: noop, handleLogout: noop,
}

describe('platform shortcut labels', () => {
  const labels = [
    ['Ctrl Shift `', '⌘⇧`'], ['Ctrl N', '⌘N'], ['Ctrl S', '⌘S'], ['Ctrl O', '⌘O'],
    ['Ctrl `', 'Ctrl `'], ['Ctrl X', 'Ctrl X'],
  ] as const

  it.each(labels)('keeps %s unchanged off Mac', (input) => {
    expect(platformKeys(input, false)).toBe(input)
  })

  it.each(labels)('renders the Mac form of %s', (input, expected) => {
    expect(platformKeys(input, true)).toBe(expected)
  })

  it('matches whole tokens and handles the longest shortcut in mixed copy', () => {
    expect(platformKeys('Ctrl Shift `, Ctrl S; (Ctrl N), Ctrl O. Ctrl ` / Ctrl X', true))
      .toBe('⌘⇧`, ⌘S; (⌘N), ⌘O. Ctrl ` / Ctrl X')
    const other = 'xCtrl N Ctrl NX Ctrl Save Ctrl Open Ctrl Shift `x Ctrl n Ctrl+N'
    expect(platformKeys(other, true)).toBe(other)
  })

  it.each(['MacIntel', 'iPhone', 'iPad', 'Win32', 'Linux x86_64'])('detects %s', platform => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform)
    expect(isMacPlatform()).toBe(/Mac|iPhone|iPad/.test(platform))
  })

  it('falls back to the user agent when platform is empty', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('')
    const agent = vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Macintosh')
    expect(isMacPlatform()).toBe(true)
    agent.mockReturnValue('Windows')
    expect(isMacPlatform()).toBe(false)
  })

  it('is safe without navigator', () => {
    vi.stubGlobal('navigator', undefined)
    expect(isMacPlatform()).toBe(false)
    expect(platformKeys('Ctrl N')).toBe('Ctrl N')
  })

  it.each([['MacIntel', '⌘N'], ['Win32', 'Ctrl N']])('formats guide and sidebar labels on %s', (platform, expected) => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform)
    const copy = { tr: 'Ctrl N yeni sohbet', en: 'Ctrl N is a new chat' }
    expect(tx(copy, 'tr')).toBe(`${expected} yeni sohbet`)
    expect(tx(copy, 'en')).toBe(`${expected} is a new chat`)
    expect(tx(undefined, 'en')).toBe('')
    expect(copy.en).toBe('Ctrl N is a new chat')
    const { container } = render(<Sidebar {...sidebarProps} />)
    expect(container.querySelector('.new-chat kbd')?.textContent).toBe(expected)
  })
})

describe('Pafta last-project layout', () => {
  const css = readFileSync('renderer/styles/gm/welcome.css', 'utf8')
  const rule = (selector: string) => {
    const start = css.indexOf(`${selector} {`)
    expect(start).toBeGreaterThanOrEqual(0)
    return css.slice(start + selector.length + 2, css.indexOf('}', start)).trim()
  }

  it('puts the tag in its own grid row above the name', () => {
    const tag = rule('[data-theme="pafta"] .wl-proj-tag')
    expect(tag).toContain('position: static;')
    expect(tag).toContain('grid-column: 2;')
    expect(tag).toContain('grid-row: 1;')
    expect(rule('[data-theme="pafta"] .wl-proj-name')).toContain('padding-right: 28px;')
    expect(rule('[data-theme="pafta"] .wl-proj.is-last .wl-proj-name')).toContain('grid-row: 2;')
  })

  it('preserves the shared absolute tag rule exactly', () => {
    expect(rule('.wl-proj-tag')).toBe(
      'position: absolute; top: 12px; right: 44px; color: var(--accent-text); font-family: var(--font-label); font-size: var(--fs-meta);\n'
      + '  font-weight: var(--label-weight); text-transform: var(--label-case); letter-spacing: var(--label-track); font-stretch: var(--label-stretch);',
    )
  })
})
