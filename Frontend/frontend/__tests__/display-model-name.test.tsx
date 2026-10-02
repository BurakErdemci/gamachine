/**
 * The model chip names the row of the ACTIVE provider.
 *
 * Owner report (2 Oct 2026): with Claude Code picked, the chip read
 * "Anthropic: Claude Sonnet 5.5". The same id is also an Anthropic API row
 * named after OpenRouter, and the cloud list was searched first.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, cleanup, act } from '@testing-library/react'
import axios from 'axios'

import { useAIConfig } from '../renderer/hooks/home/useAIConfig'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const KULLANICI = { id: 1, name: 'x', sessionToken: 't' }
const CATALOG = {
  local: [],
  cloud: [{ id: 'claude-sonnet-5-5', name: 'Anthropic: Claude Sonnet 5.5', provider: 'anthropic' }],
  subscription: [{ id: 'claude-sonnet-5-5', name: 'Claude Sonnet 5.5 (CLI)', provider: 'subscription' }],
}

async function hookWith(provider_type: string) {
  vi.spyOn(axios, 'get').mockImplementation(async (url: any) =>
    (String(url).includes('/available-models') ? { data: CATALOG } : { data: {} }) as any)
  const { result } = renderHook(() => useAIConfig('http://x', KULLANICI as any, vi.fn()))
  await act(async () => { await result.current.fetchAvailableModels() })
  // Gate: the catalogue really loaded, or the name below proves nothing.
  expect(result.current.availableModels.subscription?.length).toBe(1)
  act(() => {
    result.current.setAiConfig((c: any) => ({ ...c, provider_type, model_name: 'claude-sonnet-5-5' }))
  })
  return result
}

describe('displayModelName', () => {
  it('a CLI pick shows the subscription row name', async () => {
    const result = await hookWith('subscription')
    expect(result.current.displayModelName).toBe('Claude Sonnet 5.5 (CLI)')
  })

  it('an API pick of the same id still shows the API row name', async () => {
    const result = await hookWith('anthropic')
    expect(result.current.displayModelName).toBe('Anthropic: Claude Sonnet 5.5')
  })
})
