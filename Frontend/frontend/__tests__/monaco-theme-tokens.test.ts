/**
 * Monaco takes its colours from the editor tokens (tokens.css `--ed-*` / `--diff-*`) instead of
 * a fixed VS Code palette, and the paper themes (Pafta, Atölye) get Monaco's light base. A theme
 * switch redefines the theme without a reload.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('@monaco-editor/react', () => ({ loader: { config: () => {} } }))

import {
  EDITOR_TOKEN_DEFAULTS, monacoThemeFromTokens, defineUnityTheme, THEME_NAME,
} from '../renderer/components/home/monaco-theme'

// Pafta's editor sheet (tokens.css): a light editor with ink syntax.
const pafta = {
  ...EDITOR_TOKEN_DEFAULTS,
  '--ed-bg': '#f0ece3', '--ed-text': '#18212c', '--ed-dim': '#59636f', '--ed-kw': '#2c5b8a',
  '--diff-add-bg': '#dce6d8', '--diff-del-bg': '#f2dcd2',
}

afterEach(() => {
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.style.removeProperty('--ed-bg')
})

describe('Monaco theme from tokens', () => {
  it('a dark editor ground keeps the dark base and the token colours', () => {
    const th = monacoThemeFromTokens({ ...EDITOR_TOKEN_DEFAULTS })
    expect(th.base).toBe('vs-dark')
    expect(th.colors['editor.background']).toBe('#141925')
    expect(th.rules.find(r => r.token === 'keyword')?.foreground).toBe('8fb3f0')
  })

  it('a paper editor ground gets the light base (Pafta, Atölye)', () => {
    const th = monacoThemeFromTokens(pafta)
    expect(th.base).toBe('vs')
    expect(th.colors['editor.background']).toBe('#f0ece3')
    expect(th.colors['editorLineNumber.foreground']).toBe('#59636f')
    expect(th.colors['diffEditor.insertedLineBackground']).toBe('#dce6d8')
    expect(th.rules.find(r => r.token === 'keyword')?.foreground).toBe('2c5b8a')
    // bracket pairs in the editor ink, not Monaco's own red / gold / blue
    expect(th.colors['editorBracketHighlight.foreground1']).toBe('#18212c')
    expect(th.colors['editorBracketHighlight.foreground2']).toBe('#18212c')
  })

  it('redefines the theme when the appearance changes', async () => {
    const defineTheme = vi.fn()
    const setTheme = vi.fn()
    const monaco: any = { editor: { defineTheme, setTheme, remeasureFonts: vi.fn() } }
    defineUnityTheme(monaco)
    expect(defineTheme).toHaveBeenCalledTimes(1)
    expect(defineTheme.mock.calls[0][1].base).toBe('vs-dark')

    document.documentElement.style.setProperty('--ed-bg', '#f0ece3')
    document.documentElement.setAttribute('data-theme', 'pafta')
    await Promise.resolve()
    const last = defineTheme.mock.calls.at(-1)!
    expect(last[0]).toBe(THEME_NAME)
    expect(last[1].base).toBe('vs')
    expect(setTheme).toHaveBeenLastCalledWith(THEME_NAME)
  })
})
