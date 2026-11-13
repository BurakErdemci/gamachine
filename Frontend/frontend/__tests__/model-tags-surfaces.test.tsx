/** The tag wording reaches the rendered Settings select and the phone sheet, in both languages. */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }))

import axios from 'axios'
import { ModelsPage } from '../renderer/components/home/settings/ModelsPage'
import { PhoneMenuSheet, type PhoneMenuSheetProps } from '../renderer/components/phone/PhoneMenuSheet'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const models = {
  local: [], cloud: [],
  subscription: [
    { id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', tag: 'recommended', provider: 'subscription' },
    { id: 'gemini-3.8-flash-lite', name: 'Gemini Lite', tag: 'free', provider: 'subscription' },
  ],
} as any

beforeEach(() => { vi.mocked(axios.get).mockResolvedValue({ data: { status: 'off' } }) })
afterEach(() => { cleanup(); aktifDilAyarla('tr') })

const cases: Array<['en' | 'tr', string, string]> = [
  ['en', 'Gemini 3.8 Flash (Recommended)', 'Gemini Lite (Free)'],
  ['tr', 'Gemini 3.8 Flash (Önerilen)', 'Gemini Lite (Ücretsiz)'],
]

describe('tag wording on rendered surfaces', () => {
  it.each(cases)('Settings default-model select, %s', async (lang, rec, free) => {
    aktifDilAyarla(lang)
    render(<ModelsPage aiConfig={{ provider_type: 'subscription', model_name: 'gemini-3.8-flash', api_key: '' } as any}
      availableModels={models} providersWithKeys={[]} onSaveDefaultModel={vi.fn(async () => true)} onDeleteKey={vi.fn(async () => true)} saved={vi.fn()} />)
    await act(async () => { await Promise.resolve() })
    const labels = Array.from(screen.getByTestId('default-model-select').querySelectorAll('option')).map(o => o.textContent)
    expect(labels).toContain(rec)
    expect(labels).toContain(free)
  })

  it.each(cases)('phone sheet model list, %s', (lang, rec, free) => {
    aktifDilAyarla(lang)
    const props = {
      open: true, onClose: vi.fn(), convId: 7, models,
      currentModel: { provider_type: 'subscription', model_name: 'gemini-3.8-flash' },
      onOpenModels: vi.fn(), onModelChanged: vi.fn(), effortLevels: ['auto'], thinkingLevel: 'auto', onEffortChanged: vi.fn(),
      mode: 'step', loading: false, isCompacting: false, onCompact: vi.fn(), onStop: vi.fn(), showToast: vi.fn(), request: vi.fn(),
    } as unknown as PhoneMenuSheetProps
    render(<PhoneMenuSheet {...props} />)
    fireEvent.click(document.querySelector('[data-row="model"]') as HTMLElement)
    expect(screen.getByText(rec)).toBeTruthy()
    expect(screen.getByText(free)).toBeTruthy()
  })
})
