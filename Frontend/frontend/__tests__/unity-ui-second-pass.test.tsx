/**
 * Second pass on the first-time Unity flows (12 Nov 2026, installed 4.0.0):
 * - the model button's name truncates before the usage bars instead of running under them
 * - the give-up reason is in the UI language, with its own word; port-taken keeps "Blocked"
 * - the toggle sends the stored UI language, not the 'en' the app announces before it has loaded
 * - product and model names in the message header keep their own letters under lang="tr"
 * - the hierarchy panel's Connect never turns an on-the-way link off
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, act, renderHook } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn(); const post = vi.fn(); const put = vi.fn()
  return { default: { get, post, put, defaults: { headers: { common: {} } } }, get, post, put }
})

import axios from 'axios'
import { ModelSelector } from '../renderer/components/home/ModelSelector'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { useAIConfig, unityConnectable } from '../renderer/hooks/home/useAIConfig'
import { LangContext, aktifDilAyarla, ceviriUygula, translations } from '../renderer/lib/i18n'

const GM = resolve(__dirname, '../renderer/styles/gm')
const css = (file: string) => readFileSync(resolve(GM, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
/** The declarations of every rule whose selector is exactly `selector`. */
const rule = (source: string, selector: string) => {
  const escaped = selector.replace(/[.*+?^()|[\]\\]/g, '\\$&')
  return [...source.matchAll(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, 'g'))].map(m => m[1]).join(' ')
}
const inLang = (lang: 'tr' | 'en', ui: React.ReactElement) => (
  <LangContext.Provider value={{ lang, setLang: vi.fn(), t: (k: any, v?: any) => ceviriUygula(lang, k, v) }}>{ui}</LangContext.Provider>
)

beforeEach(() => { localStorage.clear(); vi.mocked(axios.get).mockReset(); vi.mocked(axios.post).mockReset() })
afterEach(() => { cleanup(); aktifDilAyarla(null) })

describe('model button label', () => {
  const props = () => ({
    aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5-5', api_key: '' }, setAiConfig: vi.fn(),
    availableModels: { local: [], subscription: [{ id: 'claude-opus-5-5', name: 'Claude Opus 5.5 (CLI)', provider: 'subscription' }], cloud: [] },
    providersWithKeys: [], effectiveProvider: 'subscription', displayModelName: 'Gemini 4 Argon (Medium)',
    isModelDropdownOpen: false, setIsModelDropdownOpen: vi.fn(), modelOrToggles: {}, setModelOrToggles: vi.fn(),
    user: { id: 1, name: 'B', sessionToken: 't' }, fetchAvailableModels: vi.fn(), setShowSettings: vi.fn(), API: 'http://x',
    axios: { get: vi.fn(async () => ({ data: { models: [] } })), post: vi.fn(async () => ({ data: {} })) },
    showToast: vi.fn(), conversationId: 7,
    usage: { now: '2026-10-02T12:00:00Z', families: [{ family: 'claude', status: 'ok', plan: 'Max', measured_at: '2026-10-02T11:58:00Z', stale: false, error: null,
      windows: [{ id: 'five_hour', group: null, label: '5h', kind: '5h', used_pct: 62, resets_at: null, resets_text: null }] }] },
  }) as any

  it('the name has its own box, the bars are a sibling, and the full name is the tooltip', () => {
    aktifDilAyarla('en')
    render(<ModelSelector {...props()} />)
    const button = screen.getByTestId('model-pick')
    const name = button.querySelector('.model-name') as HTMLElement
    const text = name.closest('.model-text') as HTMLElement
    const bars = button.querySelector('.chip-use') as HTMLElement
    expect(bars).not.toBeNull()
    expect(text.parentElement).toBe(button)
    expect(bars.parentElement).toBe(button)
    expect(text.contains(bars)).toBe(false)
    expect(button.getAttribute('title')).toBe(name.textContent)
    expect(name.textContent).toContain('Argon')
  })

  it('the stylesheet ends a long name in an ellipsis in the top bar and never shrinks the bars', () => {
    const named = rule(css('settings.css'), '.topbar .model-pick .model-name')
    expect(named).toMatch(/overflow:\s*hidden/)
    expect(named).toMatch(/text-overflow:\s*ellipsis/)
    expect(rule(css('settings.css'), '.topbar .model-pick .model-text')).toMatch(/min-width:\s*0/)
    expect(rule(css('shell.css'), '.model-pick .chip-use')).toMatch(/flex:\s*none/)
  })
})

describe('Unity give-up text and label', () => {
  const RAW = 'The Unity connection server kept shutting down and was not restarted again.'
  const toggleBase = { toggling: false, error: null, onToggle: vi.fn() }

  it('the give-up reason is the UI language\'s, never the backend\'s text, with its own word', () => {
    render(inLang('tr', <UnityMcpToggle {...toggleBase} status="blocked" reason={RAW} reasonCode="supervisor_gave_up" />))
    const bay = screen.getByTestId('unity-bay')
    expect(bay.textContent).not.toContain('kept shutting down')
    expect(bay.querySelector('.unity-word')!.textContent).toBe(translations.tr['unity.wordGaveUp'])
    expect(bay.querySelector('.unity-word')!.textContent).not.toBe(translations.tr['unity.wordBlocked'])
    const why = bay.querySelector('.unity-why')!.textContent!
    expect(why).toContain(translations.tr['unity.gaveUpWhyTitle'])
    expect(why).toContain(translations.tr['home.unityGaveUp'])
  })

  it('a taken port keeps "Blocked" and the backend\'s reason', () => {
    render(inLang('en', <UnityMcpToggle {...toggleBase} status="blocked" reason="Port 8080 is held by node (PID 9)" reasonCode="port_in_use" />))
    const bay = screen.getByTestId('unity-bay')
    expect(bay.querySelector('.unity-word')!.textContent).toBe(translations.en['unity.wordBlocked'])
    expect(bay.querySelector('.unity-why')!.textContent).toContain('Port 8080 is held by node (PID 9)')
  })

  it('both languages carry the new keys', () => {
    for (const lang of ['tr', 'en'] as const) {
      expect(translations[lang]['unity.wordGaveUp']).toBeTruthy()
      expect(translations[lang]['unity.gaveUpWhyTitle']).toBeTruthy()
    }
  })

  it('the toggle sends the stored UI language while the app still announces the default', async () => {
    localStorage.setItem('app-lang', 'tr')
    aktifDilAyarla('en') // home.tsx's first render, before it has read the stored choice
    vi.mocked(axios.get).mockResolvedValue({ data: { status: 'off' } })
    vi.mocked(axios.post).mockResolvedValue({ data: {} })
    const { result } = renderHook(() => useAIConfig('http://127.0.0.1:8000', null, vi.fn(), null))
    await waitFor(() => expect(axios.get).toHaveBeenCalled())
    await act(async () => { await result.current.toggleUnityMcp() })
    const call = vi.mocked(axios.post).mock.calls.find(c => String(c[0]).endsWith('/mcp/unity/toggle'))!
    expect((call[1] as any).language).toBe('tr')
  })
})

describe('product and model names in the message header', () => {
  // Uppercasing under lang="tr" turns the i of "Antigravity" or "gemini" into İ; the name is
  // English, so it is marked lang="en" and keeps its uppercase look with English casing.
  it('the author name is marked English so uppercasing keeps its own letters', () => {
    const src = readFileSync(resolve(__dirname, '../renderer/components/home/ChatPanel.tsx'), 'utf8')
    expect(src).toMatch(/className="who-name"[^>]*lang="en"/)
    expect(rule(css('thread.css'), '.msg-who .who-name') ?? '').not.toMatch(/text-transform:\s*none/)
  })
})

describe('hierarchy panel Connect', () => {
  it.each(['starting', 'running', 'connected', 'unknown'] as const)('does not reach the toggle while the link is %s', (status) => {
    expect(unityConnectable(status)).toBe(false)
  })

  it.each(['off', 'blocked'] as const)('connects when the link is %s', (status) => {
    expect(unityConnectable(status)).toBe(true)
  })

  it('the page routes the panel Connect through that guard', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    const line = home.split('\n').find(l => l.includes('onConnect='))!
    expect(line).toMatch(/unityConnectable\(ai\.unityMcpStatus\)/)
    expect(line).toMatch(/toggleUnityMcp/)
  })
})
