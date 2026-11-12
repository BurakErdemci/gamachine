/**
 * Model badges ("Recommended", "Free") arrive as data and are worded by the UI language;
 * the chip names a live-listed model (OpenCode) the way the picker does.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, act, within } from '@testing-library/react'

import { ModelSelector } from '@/components/home/ModelSelector'
import { modelPicks } from '@/components/phone/PhoneMenuSheet'
import { modelLabel } from '@/lib/modelText'
import { aktifDilAyarla, ceviriUygula } from '@/lib/i18n'

afterEach(() => { cleanup(); aktifDilAyarla('tr') })

const flush = () => act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve() })

describe('modelLabel', () => {
  const t = (lang: 'en' | 'tr') => (k: any) => ceviriUygula(lang, k)
  it('words the tag in the UI language and leaves an untagged name alone', () => {
    const m = { name: 'Gemini 3.8 Flash', tag: 'recommended' }
    expect(modelLabel(m, t('en'))).toBe('Gemini 3.8 Flash (Recommended)')
    expect(modelLabel(m, t('tr'))).toBe('Gemini 3.8 Flash (Önerilen)')
    expect(modelLabel({ name: 'Big Pickle', tag: 'free' }, t('en'))).toBe('Big Pickle (Free)')
    expect(modelLabel({ name: 'Big Pickle', tag: 'free' }, t('tr'))).toBe('Big Pickle (Ücretsiz)')
    expect(modelLabel({ name: 'Kimi K3 (Go)' }, t('en'))).toBe('Kimi K3 (Go)')
  })
})

const base = (over: Record<string, unknown> = {}) => ({
  aiConfig: { provider_type: 'subscription', model_name: 'gemini-3.8-flash', api_key: '' },
  setAiConfig: vi.fn(),
  availableModels: {
    local: [], cloud: [],
    subscription: [
      { id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', tag: 'recommended', provider: 'subscription' },
      { id: 'gemini-3.8-flash-medium', name: 'Gemini 3.8 Flash (Medium)', provider: 'subscription' },
    ],
  },
  providersWithKeys: [], effectiveProvider: 'subscription', displayModelName: 'Gemini 3.8 Flash',
  isModelDropdownOpen: true, setIsModelDropdownOpen: vi.fn(), modelOrToggles: {}, setModelOrToggles: vi.fn(),
  user: { id: 1, name: 'B', sessionToken: 't' }, fetchAvailableModels: vi.fn(), setShowSettings: vi.fn(),
  API: 'http://x',
  axios: {
    get: vi.fn(async (url: string) => url.includes('/cli-doctor')
      ? { data: { cursor: { installed: true, loggedIn: true }, opencode: { installed: true, loggedIn: true } } }
      : url.includes('/cli-models/opencode')
        ? { data: { models: [{ id: 'opencode:opencode/big-pickle', name: 'Big Pickle', tag: 'free', provider: 'subscription' }] } }
        : { data: { models: [] } }),
    post: vi.fn(async () => ({ data: {} })),
  },
  showToast: vi.fn(), conversationId: 7, usage: null,
  ...over,
}) as any

describe('the picker words the tag', () => {
  it('English: Recommended, no Turkish suffix', async () => {
    aktifDilAyarla('en')
    render(<ModelSelector {...base()} />)
    await flush()
    const pane = screen.getByTestId('mm-pane')
    const row = within(pane).getByRole('option', { name: /^Gemini 3\.8 Flash \(Recommended\)$/ })
    expect(row.textContent).not.toMatch(/Önerilen|Ücretsiz/)
  })
  it('Turkish: Önerilen', async () => {
    aktifDilAyarla('tr')
    render(<ModelSelector {...base()} />)
    await flush()
    expect(within(screen.getByTestId('mm-pane')).getByRole('option', { name: /^Gemini 3\.8 Flash \(Önerilen\)$/ })).toBeTruthy()
  })
  it('the phone sheet labels follow the same words', () => {
    const t = (k: any) => ceviriUygula('en', k)
    const picks = modelPicks(base().availableModels as any, t)
    expect(picks[0].label).toBe('Gemini 3.8 Flash (Recommended)')
    expect(modelPicks(base().availableModels as any)[0].label).toBe('Gemini 3.8 Flash')
  })
})

describe('the model button', () => {
  it('names a live-listed model like the picker does, not by its raw id', async () => {
    aktifDilAyarla('en')
    const props = base({
      aiConfig: { provider_type: 'subscription', model_name: 'opencode:opencode/big-pickle', api_key: '' },
      displayModelName: 'big-pickle', isModelDropdownOpen: false,
    })
    render(<ModelSelector {...props} />)
    await flush()
    expect(screen.getByTestId('model-pick').querySelector('.model-name')!.textContent).toBe('Big Pickle')
  })
  it('falls back to the id when no list knows the model', async () => {
    aktifDilAyarla('en')
    const props = base({
      aiConfig: { provider_type: 'subscription', model_name: 'opencode:opencode/mystery-model', api_key: '' },
      displayModelName: 'mystery-model', isModelDropdownOpen: false,
    })
    render(<ModelSelector {...props} />)
    await flush()
    expect(screen.getByTestId('model-pick').querySelector('.model-name')!.textContent).toBe('mystery-model')
  })
})
