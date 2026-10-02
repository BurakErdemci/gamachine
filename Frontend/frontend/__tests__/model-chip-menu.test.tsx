/**
 * Round 11 model chip and two-pane model menu (mockup `.model-pick` / `.model-menu`).
 *  - the chip: the provider's official mark + the model name only (no provider text, no
 *    uppercase, never truncated) + two usage hairlines for the active family;
 *  - the menu: providers on the left (plan + two mini meters, or the key / local / sign-in
 *    line), the picked provider's usage box and models on the right, the thinking level synced
 *    with the composer's Düşünme control, "Usage and accounts" opening the Modeller page.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React, { useState } from 'react'
import { render, screen, cleanup, fireEvent, act, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { ModelSelector } from '../renderer/components/home/ModelSelector'
import { ControlPanel, type ThinkingLevel } from '../renderer/components/home/ControlPanel'
import { chipModelName } from '../renderer/components/home/providerGroups'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import type { UsageLimits } from '../renderer/lib/usageLimits'

const USER = { id: 1, name: 'B', sessionToken: 't' }
const NOW = '2026-10-02T12:00:00Z'
const USAGE: UsageLimits = {
  now: NOW,
  families: [
    { family: 'claude', status: 'ok', plan: 'Max', measured_at: '2026-10-02T11:58:00Z', stale: false, error: null,
      windows: [
        { id: 'five_hour', group: null, label: '5h', kind: '5h', used_pct: 62, resets_at: '2026-10-02T15:40:00Z', resets_text: null },
        { id: 'week', group: null, label: 'week', kind: 'week', used_pct: 41, resets_at: '2026-10-06T07:00:00Z', resets_text: null },
      ] },
    { family: 'codex', status: 'unavailable', plan: null, measured_at: null, stale: false, error: null, windows: [] },
  ],
}

const props = (over: Record<string, unknown> = {}) => ({
  aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5-5', api_key: '' },
  setAiConfig: vi.fn(),
  availableModels: {
    local: [{ id: 'qwen3:8b', name: 'Qwen3 8B', provider: 'ollama' }],
    subscription: [
      { id: 'claude-opus-5-5', name: 'Claude Opus 5.5 (CLI)', provider: 'subscription' },
      { id: 'claude-sonnet-5-5', name: 'Claude Sonnet 5.5 (CLI)', provider: 'subscription' },
    ],
    cloud: [{ id: 'gpt-9', name: 'GPT-9', provider: 'openai' }],
  },
  providersWithKeys: ['openai'],
  effectiveProvider: 'subscription',
  displayModelName: 'Claude Opus 5.5 (CLI)',
  isModelDropdownOpen: false,
  setIsModelDropdownOpen: vi.fn(),
  modelOrToggles: {},
  setModelOrToggles: vi.fn(),
  user: USER,
  fetchAvailableModels: vi.fn(),
  setShowSettings: vi.fn(),
  API: 'http://x',
  axios: {
    get: vi.fn(async (url: string) => url.includes('/cli-doctor')
      ? { data: { claude: { installed: true, loggedIn: true }, cursor: { installed: true, loggedIn: false }, codex: { installed: true, loggedIn: true } } }
      : { data: { models: [] } }),
    post: vi.fn(async () => ({ data: {} })),
  },
  showToast: vi.fn(),
  conversationId: 7,
  usage: USAGE,
  ...over,
}) as any

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

beforeEach(() => aktifDilAyarla('tr'))
afterEach(() => cleanup())

describe('the chip', () => {
  it('shows the mark and the model name only: no provider text, no "(CLI)"', () => {
    render(<ModelSelector {...props()} />)
    const chip = screen.getByTestId('model-pick')
    expect(chip.querySelector('.model-name')!.textContent).toBe('Opus 5.5')
    expect(chip.querySelector('.model-sub')).toBeNull()
    expect(chip.querySelector('svg.plogo')).toBeTruthy()
    // The provider's name lives only in the screen-reader line of the meters.
    const visible = Array.from(chip.querySelectorAll(':scope > :not(.sr-only)')).map(n => n.textContent).join('')
    expect(visible).not.toContain('Claude Code')
    expect(visible).not.toContain('Claude Opus')
  })

  it('the name is never case-transformed or truncated by the stylesheet', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/settings.css'), 'utf8')
    const rule = css.match(/\.model-pick \.model-name\s*\{([^}]*)\}/)![1]
    expect(rule).toMatch(/text-transform:\s*none/)
    expect(rule).toMatch(/white-space:\s*nowrap/)
    expect(rule).toMatch(/text-overflow:\s*clip/)
    expect(css).toMatch(/\.model-pick\s*\{[^}]*max-width:\s*none/)
  })

  it('names: the mark already says the provider', () => {
    expect(chipModelName('Claude Opus 5.5 (CLI)', true)).toBe('Opus 5.5')
    expect(chipModelName('Codex (GPT-6 Sol)')).toBe('GPT-6 Sol')
    expect(chipModelName('Gemini 3.1 Pro (High)')).toBe('Gemini 3.1 Pro (High)')
    // Under Antigravity's mark "Claude" is the information, so it stays.
    expect(chipModelName('Claude Sonnet 4.6 (Thinking)', false)).toBe('Claude Sonnet 4.6 (Thinking)')
    expect(chipModelName('Claude 4.8 Opus (CLI)', true)).toBe('Claude 4.8 Opus')
  })

  it('two hairlines for the active family; none for a family without numbers', () => {
    render(<ModelSelector {...props()} />)
    const bars = screen.getByTestId('chip-use').querySelectorAll('i')
    expect((bars[0] as HTMLElement).style.getPropertyValue('--u')).toBe('62%')
    expect((bars[1] as HTMLElement).style.getPropertyValue('--u')).toBe('41%')
    cleanup()
    render(<ModelSelector {...props({ aiConfig: { provider_type: 'subscription', model_name: 'gpt-6-sol', api_key: '' }, displayModelName: 'Codex (GPT-6 Sol)' })} />)
    expect(screen.queryByTestId('chip-use')).toBeNull()
    expect(screen.getByTestId('model-pick').querySelector('.model-name')!.textContent).toBe('GPT-6 Sol')
    cleanup()
    render(<ModelSelector {...props({ aiConfig: { provider_type: 'openai', model_name: 'gpt-9', api_key: '' }, displayModelName: 'GPT-9', usage: null })} />)
    expect(screen.queryByTestId('chip-use')).toBeNull()
  })
})

describe('the two-pane menu', () => {
  it('opens on the provider of the model on screen, with its usage box and models', async () => {
    render(<ModelSelector {...props({ isModelDropdownOpen: true })} />)
    await flush()
    const claude = screen.getByTestId('mm-prov-claude')
    expect(claude.getAttribute('aria-selected')).toBe('true')
    expect(claude.textContent).toContain(cevir('mm.sub.subscriptionPlan', { plan: 'Max' }))
    expect(within(claude).getByTestId('use-pair')).toBeTruthy()
    const pane = screen.getByTestId('mm-pane')
    expect(within(pane).getByTestId('use-block').textContent).toContain('%62')
    const current = within(pane).getByRole('option', { name: /Claude Opus 5\.5/ })
    expect(current.getAttribute('aria-selected')).toBe('true')
    expect(within(pane).getByRole('option', { name: /Claude Sonnet 5\.5/ }).getAttribute('aria-selected')).toBe('false')
  })

  it('the left column says what each provider has: meters, key, local, sign-in', async () => {
    render(<ModelSelector {...props({ isModelDropdownOpen: true })} />)
    await flush()
    // Codex is "unavailable" in the usage answer: a plan line, no meters.
    expect(within(screen.getByTestId('mm-prov-codex')).queryByTestId('use-pair')).toBeNull()
    expect(screen.getByTestId('mm-prov-cloud:openai').textContent).toContain(cevir('mm.sub.apiKey'))
    expect(screen.getByTestId('mm-prov-local').textContent).toContain(cevir('use.unlimited'))
    const cursor = screen.getByTestId('mm-prov-cursor')
    expect(cursor.textContent).toContain(cevir('mm.sub.needsLogin'))
    expect(cursor.classList.contains('is-off')).toBe(true)
  })

  it('picking a provider shows its pane; a CLI that needs sign-in offers it', async () => {
    const p = props({ isModelDropdownOpen: true })
    render(<ModelSelector {...p} />)
    await flush()
    fireEvent.click(screen.getByTestId('mm-prov-cursor'))
    expect(screen.getByTestId('mm-pane').getAttribute('data-prov')).toBe('cursor')
    await act(async () => { fireEvent.click(screen.getByTestId('mm-login')) })
    expect(p.axios.post).toHaveBeenCalledWith('http://x/cli-login/cursor', null, { headers: { 'X-Session-Token': 't' } })
    fireEvent.click(screen.getByTestId('mm-prov-local'))
    expect(within(screen.getByTestId('mm-pane')).getByText('Qwen3 8B')).toBeTruthy()
  })

  it('a pick saves for the chat on screen and closes the menu', async () => {
    const p = props({ isModelDropdownOpen: true })
    render(<ModelSelector {...p} />)
    await flush()
    await act(async () => { fireEvent.click(screen.getByRole('option', { name: /Claude Sonnet 5\.5/ })) })
    expect(p.setIsModelDropdownOpen).toHaveBeenCalledWith(false)
    expect(p.axios.post).toHaveBeenCalledWith('http://x/save-ai-config', expect.objectContaining({
      provider_type: 'subscription', model_name: 'claude-sonnet-5-5', conversation_id: 7 }))
  })

  it('"Usage and accounts" opens the Modeller page', async () => {
    const openSettings = vi.fn()
    const p = props({ isModelDropdownOpen: true, openSettings })
    render(<ModelSelector {...p} />)
    await flush()
    fireEvent.click(screen.getByTestId('mm-usage-link'))
    expect(p.setIsModelDropdownOpen).toHaveBeenCalledWith(false)
    expect(openSettings).toHaveBeenCalledWith('modeller')
    expect(screen.getByText(cevir('use.measured', { dk: 2 }))).toBeTruthy()
  })

  it('a keyless cloud pick sends the user to the Modeller page', async () => {
    const openSettings = vi.fn()
    const p = props({ isModelDropdownOpen: true, openSettings, providersWithKeys: [],
      aiConfig: { provider_type: 'openai', model_name: 'gpt-9', api_key: '' } })
    render(<ModelSelector {...p} />)
    await flush()
    await act(async () => { fireEvent.click(screen.getByRole('option', { name: /GPT-9/ })) })
    expect(openSettings).toHaveBeenCalledWith('modeller')
    expect(p.setShowSettings).not.toHaveBeenCalled()
  })

  it('Esc closes the menu', async () => {
    const p = props({ isModelDropdownOpen: true })
    render(<ModelSelector {...p} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(p.setIsModelDropdownOpen).toHaveBeenCalledWith(false)
  })
})

describe('thinking level: the menu and the composer show the same value', () => {
  const Harness = () => {
    const [level, setLevel] = useState<ThinkingLevel>('medium')
    const caps = { levels: ['auto', 'low', 'medium', 'high'] }
    return (
      <>
        <ModelSelector {...props({ isModelDropdownOpen: true })} thinkingLevel={level} effortLevels={caps.levels} onThinkingChange={setLevel} />
        <ControlPanel
          thinkingLevel={level} setThinkingLevel={setLevel} effortCaps={caps}
          generationMode="balanced" setGenerationMode={() => {}} isAnalyzingProject={false} activeConvId={null}
          analyzeProject={async () => {}} exportMemory={async () => {}} importMemory={async () => {}}
          compactConversation={async () => {}} isCompacting={false}
        />
      </>
    )
  }

  it('a change in the menu shows in the composer, and back', async () => {
    render(<Harness />)
    await flush()
    const group = screen.getByRole('radiogroup', { name: cevir('mm.effortGroup') })
    const radio = (label: string) => within(group).getByRole('radio', { name: label })
    expect(radio(cevir('effort.label.medium')).getAttribute('aria-checked')).toBe('true')
    const strip = () => screen.getByTestId('composer-strip').querySelector('.strip-item') as HTMLElement
    expect(strip().getAttribute('data-level')).toBe('medium')

    fireEvent.click(radio(cevir('effort.label.high')))
    expect(strip().getAttribute('data-level')).toBe('high')
    expect(radio(cevir('effort.label.high')).getAttribute('aria-checked')).toBe('true')

    // The composer's own control writes the same state.
    fireEvent.click(strip())
    const pop = document.querySelector('.strip-effort') as HTMLElement
    fireEvent.click(within(pop).getByRole('button', { name: cevir('effort.label.low') }))
    expect(radio(cevir('effort.label.low')).getAttribute('aria-checked')).toBe('true')
  })

  it('a model with no effort control shows the single level, not a switch', async () => {
    const onThinkingChange = vi.fn()
    render(<ModelSelector {...props({ isModelDropdownOpen: true })} thinkingLevel="auto" effortLevels={null} onThinkingChange={onThinkingChange} />)
    await flush()
    const only = within(screen.getByTestId('mm-effort')).getAllByRole('radio')
    expect(only).toHaveLength(1)
    expect((only[0] as HTMLButtonElement).disabled).toBe(true)
  })
})
