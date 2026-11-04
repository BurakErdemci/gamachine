// The phone's "⋯" sheet (renderer/components/phone/PhoneMenuSheet.tsx): model, thinking level and approval mode
// go through the v1 RPCs set_model / set_effort / set_approval_mode with the backend's parameters (rpc.py);
// Auto asks first inside the app; Compact and Stop are the page's own handlers.
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { PhoneMenuSheet, type PhoneMenuSheetProps, type PhoneRequest } from '../renderer/components/phone/PhoneMenuSheet'
import { RemoteError } from '../renderer/lib/remote/transport'
import { aktifDilAyarla, translations } from '../renderer/lib/i18n'

const tr = translations.tr
let request: Mock<PhoneRequest>

beforeEach(() => {
  aktifDilAyarla('tr')
  request = vi.fn<PhoneRequest>(async (op) => (op === 'set_effort' ? { status: 'accepted' } : { ok: true }))
})
afterEach(cleanup)

function sheet(over: Partial<PhoneMenuSheetProps> = {}) {
  const props: PhoneMenuSheetProps = {
    open: true, onClose: vi.fn(), convId: 7,
    models: {
      subscription: [{ id: 'claude-sonnet-4-6', name: 'Claude Sonnet', provider: 'anthropic' }],
      local: [{ id: 'llama3', name: 'Llama 3', provider: 'ollama' }],
      cloud: [
        { id: 'gpt-x', name: 'GPT X', provider: 'openai', available: true },
        { id: 'catalog-only', name: 'Catalog only', provider: 'openrouter', verified: false },
      ],
    },
    currentModel: { provider_type: 'subscription', model_name: 'claude-sonnet-4-6' },
    onOpenModels: vi.fn(), onModelChanged: vi.fn(),
    effortLevels: ['auto', 'low', 'high'], thinkingLevel: 'auto', onEffortChanged: vi.fn(),
    mode: 'step', loading: true, isCompacting: false, onCompact: vi.fn(), onStop: vi.fn(),
    showToast: vi.fn(), request,
    ...over,
  }
  render(<PhoneMenuSheet {...props} />)
  return props
}
const row = (key: string) => document.querySelector(`[data-row="${key}"]`) as HTMLButtonElement

describe('the menu', () => {
  it('lists Model, Düşünme, Onay modu, Compact, Durdur with the current values', () => {
    sheet()
    expect(screen.getByRole('dialog', { name: tr['phone.menu'] })).toBeTruthy()
    expect(row('model').textContent).toContain('Claude Sonnet')
    expect(row('effort').textContent).toContain(tr['effort.label.auto'])
    expect(row('mode').textContent).toContain(tr['mode.step'])
    expect(row('compact').textContent).toBe(tr['phone.sheet.compact'])
    expect(row('stop').textContent).toBe(tr['phone.sheet.stop'])
  })

  it('Compact and Durdur run the page handlers; Durdur is off while nothing runs', () => {
    const p = sheet()
    fireEvent.click(row('compact'))
    expect(p.onCompact).toHaveBeenCalledTimes(1)
    fireEvent.click(row('stop'))
    expect(p.onStop).toHaveBeenCalledTimes(1)
    expect(request).not.toHaveBeenCalled()
    cleanup()
    sheet({ loading: false })
    expect(row('stop').disabled).toBe(true)
  })
})

describe('set_model', () => {
  it('re-reads the list on opening and sends the chat, provider and model of the pick', async () => {
    const p = sheet()
    fireEvent.click(row('model'))
    expect(p.onOpenModels).toHaveBeenCalled()
    // An unreachable / catalogue-only cloud row is not offered.
    expect(screen.queryByText('Catalog only')).toBeNull()
    fireEvent.click(screen.getByText('Llama 3'))
    await waitFor(() => expect(request).toHaveBeenCalledWith('set_model', { chat_id: 7, provider_type: 'ollama', model_name: 'llama3' }))
    await waitFor(() => expect(p.onModelChanged).toHaveBeenCalled())
    expect(p.onClose).toHaveBeenCalled()
  })

  it('a refused pick says why and changes nothing', async () => {
    request.mockRejectedValueOnce(new RemoteError('plan_locked'))
    const p = sheet()
    fireEvent.click(row('model'))
    fireEvent.click(screen.getByText('GPT X'))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(tr['phone.sheet.failed'].replace('{error}', 'plan_locked'), 'error'))
    expect(request).toHaveBeenCalledWith('set_model', { chat_id: 7, provider_type: 'openai', model_name: 'gpt-x' })
    expect(p.onModelChanged).not.toHaveBeenCalled()
  })

  it('needs a chat', () => {
    sheet({ convId: null })
    expect(row('model').disabled).toBe(true)
  })
})

describe('set_effort', () => {
  it('sends the level and applies it here once the PC took it', async () => {
    const p = sheet()
    fireEvent.click(row('effort'))
    fireEvent.click(screen.getByText(tr['effort.label.high']))
    await waitFor(() => expect(request).toHaveBeenCalledWith('set_effort', { level: 'high' }))
    await waitFor(() => expect(p.onEffortChanged).toHaveBeenCalledWith('high'))
  })

  it('a PC not ready changes nothing here', async () => {
    request.mockResolvedValueOnce({ status: 'desktop_not_ready' })
    const p = sheet()
    fireEvent.click(row('effort'))
    fireEvent.click(screen.getByText(tr['effort.label.low']))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(tr['phone.sheet.notReady'], 'warning'))
    expect(p.onEffortChanged).not.toHaveBeenCalled()
  })

  it('is off for a model with one level only', () => {
    sheet({ effortLevels: ['auto'] })
    expect(row('effort').disabled).toBe(true)
  })
})

describe('set_approval_mode', () => {
  it('Dengeli goes at once', async () => {
    sheet()
    fireEvent.click(row('mode'))
    fireEvent.click(screen.getByText(tr['mode.balanced']))
    await waitFor(() => expect(request).toHaveBeenCalledWith('set_approval_mode', { mode: 'balanced' }))
  })

  it('Otomatik asks first in the app, never window.confirm; cancelling sends nothing', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    sheet()
    fireEvent.click(row('mode'))
    fireEvent.click(screen.getByText(tr['mode.auto']))
    expect(screen.getByRole('alertdialog').textContent).toContain(tr['phone.sheet.autoWarning'])
    expect(request).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText(tr['confirm.cancel']))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(request).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText(tr['mode.auto']))
    fireEvent.click(screen.getByTestId('phone-auto-confirm'))
    await waitFor(() => expect(request).toHaveBeenCalledWith('set_approval_mode', { mode: 'auto' }))
    expect(request).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})
