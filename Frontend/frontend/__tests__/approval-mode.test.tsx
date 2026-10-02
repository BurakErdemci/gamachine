/**
 * Global approval mode in the UI (closed-loop.md §5).
 *
 * The mode lives in the backend; the renderer reads it with the normal token
 * and writes it only through Electron main ('approval-mode-set'), never to
 * localStorage. The old localStorage value is migrated once.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, renderHook, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ipcInvoke = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke }
  return invoke
})

vi.mock('axios', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { default: { get, post, delete: vi.fn(), put: vi.fn() }, get, post }
})

import axios from 'axios'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { GenerationModeSelector } from '../renderer/components/home/GenerationModeSelector'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval, gateRisk, MCP_MSG_ID } from '../renderer/hooks/home/useMCPApproval'
import { CommandApproval } from '../renderer/components/home/CommandApproval'
import { FileDeleteApproval } from '../renderer/components/home/FileDeleteApproval'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { riskReasonLabel } from '../renderer/components/home/RiskReasonLine'
import { cevir, aktifDilAyarla, translations } from '../renderer/lib/i18n'

const mockedGet = (axios as any).get as ReturnType<typeof vi.fn>
const KEY = 'unityai-generation-mode'

afterEach(() => { cleanup() })

describe('Settings screen · Onay modu page', () => {
  // Round 11: the modal's "Work mode" tab became the screen's "Onay modu" page; the cards are a
  // radiogroup (aria-checked) in the mockup's order step → balanced → auto.
  const props: any = {
    open: true,
    aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
    availableModels: { local: [], subscription: [], cloud: [] },
    providersWithKeys: [],
    onClose: () => {},
    onLogout: () => {},
    onDeleteKey: async () => {},
    unityMcpStatus: 'off',
    unityMcpToggling: false,
    onToggleUnityMcp: () => {},
    lang: 'tr',
    onLangChange: () => {},
  }
  const openOnay = () => fireEvent.click(screen.getByRole('button', { name: cevir('set.nav.onay') }))

  it('the first page holds the language; Unity and the models have their own pages', () => {
    render(<SettingsScreen {...props} approvalMode="step" onApprovalModeChange={vi.fn()} />)
    expect(screen.getByText(cevir('set.uiLang'))).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Unity' }))
    expect(screen.getByTestId('unity-mcp-row')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: cevir('set.nav.modeller') }))
    expect(screen.getByTestId('default-model-select')).toBeTruthy()
  })

  it('mode page explains auto and writes through the handler', () => {
    const onChange = vi.fn()
    render(<SettingsScreen {...props} approvalMode="step" onApprovalModeChange={onChange} />)
    openOnay()
    expect(screen.getByText(cevir('settings.modeAutoExplain'))).toBeTruthy()
    fireEvent.click(screen.getByText(cevir('set.mode.autoTitle')))
    expect(onChange).toHaveBeenCalledWith('auto')
  })

  it('the auto card is red whether or not it is selected; balanced and step are not', () => {
    for (const mode of ['auto', 'balanced', 'step'] as const) {
      render(<SettingsScreen {...props} approvalMode={mode} onApprovalModeChange={vi.fn()} />)
      openOnay()
      const autoTitle = screen.getByText(cevir('set.mode.autoTitle'))
      const autoCard = autoTitle.closest('button')!
      const balancedCard = screen.getByText(cevir('set.mode.balancedTitle')).closest('button')!
      const stepCard = screen.getByText(cevir('set.mode.stepTitle')).closest('button')!
      // The colour comes from settings.css, keyed on data-warn (the rule
      // itself is checked below), so the card and its title carry the state.
      expect(autoTitle.getAttribute('data-warn')).toBe('true')
      expect(autoCard.getAttribute('data-warn')).toBe('true')
      for (const card of [balancedCard, stepCard]) {
        expect(card.getAttribute('data-warn')).toBeNull()
        expect(card.querySelector('[data-warn]')).toBeNull()
      }
      expect(screen.getByText(cevir('settings.modeStepExplain'))).toBeTruthy()
      expect(screen.getByText(cevir('settings.modeBalancedExplain'))).toBeTruthy()
      cleanup()
    }
  })

  it('shows three mode cards in order, balanced marked recommended, and writes balanced', () => {
    const onChange = vi.fn()
    render(<SettingsScreen {...props} approvalMode="step" onApprovalModeChange={onChange} />)
    openOnay()
    const titles = [cevir('set.mode.stepTitle'), cevir('set.mode.balancedTitle'), cevir('set.mode.autoTitle')]
    const cards = titles.map(title => screen.getByText(title).closest('button')!)
    // DOM order (mockup round 11): step, balanced, auto — from most to least asking.
    expect(cards[0].compareDocumentPosition(cards[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(cards[1].compareDocumentPosition(cards[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const badges = screen.getAllByTestId('mode-recommended-badge')
    expect(badges).toHaveLength(1)
    expect(badges[0].textContent).toBe(cevir('mode.recommended'))
    expect(cards[1].contains(badges[0])).toBe(true)
    fireEvent.click(cards[1])
    expect(onChange).toHaveBeenCalledWith('balanced')
  })

  it('the selected balanced card is the checked one', () => {
    render(<SettingsScreen {...props} approvalMode="balanced" onApprovalModeChange={vi.fn()} />)
    openOnay()
    const balancedCard = screen.getByText(cevir('set.mode.balancedTitle')).closest('button')!
    expect(balancedCard.getAttribute('role')).toBe('radio')
    expect(balancedCard.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText(cevir('set.mode.stepTitle')).closest('button')!.getAttribute('aria-checked')).toBe('false')
  })

  it('settings.css draws the warning and the highlight from those attributes', () => {
    // The attributes above are only worth asserting if a rule turns them into
    // colour: the warning in the danger token, the selection as an outlined card.
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/settings.css'), 'utf8')
    expect(css).toMatch(/\.mode-card\[data-warn="true"\]\[aria-checked="true"\]\s*\{[^}]*var\(--set-warn\)/)
    expect(css).toMatch(/\.mode-title\[data-warn="true"\]\s*\{[^}]*var\(--set-warn-text\)/)
    expect(css).toMatch(/\.mode-card\[aria-checked="true"\]\s*\{[^}]*border-color/)
    expect(css).toMatch(/\.mode-risk\s*\{[^}]*var\(--set-warn-text\)/)
  })

  it('the new mode-card wording is in both languages, without the removed trust line', () => {
    aktifDilAyarla('tr')
    expect(cevir('settings.modeAutoTitle')).toBe('⚠ OTO MOD — ONAY YOK')
    expect(cevir('settings.modeStepTitle')).toBe('✋ Adım adım')
    expect(cevir('settings.modeAutoExplain')).toContain('izinleri atla')
    aktifDilAyarla('en')
    try {
      expect(cevir('settings.modeAutoTitle')).toBe('⚠ AUTO MODE — NO APPROVALS')
      expect(cevir('settings.modeStepTitle')).toBe('✋ Step by step')
      expect(cevir('settings.modeAutoExplain')).toContain('bypass permissions')
    } finally {
      aktifDilAyarla('tr')
    }
    for (const lang of ['tr', 'en'] as const) {
      expect(translations[lang]['settings.modeAutoExplain']).not.toMatch(/güvendiğin|trusted/i)
    }
  })
})

describe('Auto-mode warning lives on the chat mode selector', () => {
  it('auto shows the red tone, a red dot and the warning tooltip', () => {
    render(<GenerationModeSelector value="auto" onChange={vi.fn()} />)
    const button = screen.getByText(cevir('mode.auto')).closest('button')!
    expect(button.getAttribute('title')).toBe(cevir('mode.autoWarning'))
    expect(button.className).toContain('text-red-400')
    expect(screen.getByTestId('auto-mode-dot')).toBeTruthy()
  })

  it('step keeps its plain look', () => {
    render(<GenerationModeSelector value="step" onChange={vi.fn()} />)
    const button = screen.getByText(cevir('mode.step')).closest('button')!
    expect(button.getAttribute('title')).toBeNull()
    expect(button.className).not.toContain('red')
    expect(screen.queryByTestId('auto-mode-dot')).toBeNull()
  })

  it('balanced keeps the plain look too: no red, no dot, no tooltip', () => {
    render(<GenerationModeSelector value="balanced" onChange={vi.fn()} />)
    const button = screen.getByText(cevir('mode.balanced')).closest('button')!
    expect(button.getAttribute('title')).toBeNull()
    expect(button.className).not.toContain('red')
    expect(screen.queryByTestId('auto-mode-dot')).toBeNull()
  })

  it('the dropdown lists auto, balanced, step; balanced alone is recommended and selectable', () => {
    const onChange = vi.fn()
    const { container } = render(<GenerationModeSelector value="step" onChange={onChange} />)
    fireEvent.click(screen.getByText(cevir('mode.step')).closest('button')!)
    const options = Array.from(container.querySelectorAll('[data-mode]')).map(el => el.getAttribute('data-mode'))
    expect(options).toEqual(['auto', 'balanced', 'step'])
    const badges = screen.getAllByTestId('mode-recommended-badge')
    expect(badges).toHaveLength(1)
    expect(badges[0].closest('[data-mode]')!.getAttribute('data-mode')).toBe('balanced')
    expect(screen.getByText(cevir('mode.balancedDesc'))).toBeTruthy()
    fireEvent.click(container.querySelector('[data-mode="balanced"]')!)
    expect(onChange).toHaveBeenCalledWith('balanced')
  })

  it('the balanced wording exists in both languages and never says plan', () => {
    expect(translations.tr['mode.balanced']).toBe('Güvenli Otomatik')
    expect(translations.tr['settings.modeBalancedTitle']).toBe('Güvenli Otomatik')
    expect(translations.en['mode.balanced']).toBe('Safe Auto')
    expect(translations.en['settings.modeBalancedTitle']).toBe('Safe Auto')
    expect(translations.tr['mode.recommended']).toBe('Önerilen')
    expect(translations.en['mode.recommended']).toBe('Recommended')
    expect(translations.tr['settings.modeBalancedExplain']).toContain('yalnız kritik işlemlerde')
    expect(translations.en['settings.modeBalancedExplain']).toContain('only before critical actions')
    for (const lang of ['tr', 'en'] as const) {
      for (const key of ['mode.balanced', 'mode.balancedDesc', 'settings.modeBalancedTitle', 'settings.modeBalancedExplain']) {
        expect(translations[lang][key]).not.toMatch(/plan/i)
      }
    }
  })

  it('the tooltip wording exists in both languages', () => {
    expect(translations.tr['mode.autoWarning']).toBe('Oto mod — onay kartı yok')
    expect(translations.en['mode.autoWarning']).toBe('Auto mode — no approval cards')
  })

  it('the header badge is gone', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(home).not.toContain('mode.indicator')
    expect(home).not.toContain('mode.autoWarning')
    for (const lang of ['tr', 'en'] as const) {
      expect(translations[lang]['mode.indicator']).toBeUndefined()
      expect(translations[lang]['mode.indicatorTitle']).toBeUndefined()
    }
  })
})

const hook = (showToast = vi.fn()) => renderHook(() => useChat(
  'http://127.0.0.1:8000',
  { id: 1, name: 'b', sessionToken: 'tok' } as any,
  { provider_type: 'anthropic', model_name: 'm' } as any,
  '/ws',
  showToast,
  vi.fn(),
  (n: string) => n,
))

describe('useChat · backend-owned mode', () => {
  beforeEach(() => {
    ipcInvoke.mockReset()
    mockedGet.mockReset()
    window.localStorage.clear()
  })

  it('reads the stored backend mode and drops the legacy key', async () => {
    window.localStorage.setItem(KEY, 'step')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: true } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(mockedGet.mock.calls[0][0]).toContain('/approval-mode')
    expect(ipcInvoke).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('migrates the old localStorage value once, over the fresh-install auto', async () => {
    window.localStorage.setItem(KEY, 'step')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    ipcInvoke.mockResolvedValue({ mode: 'step', previous: 'auto' })
    const { result } = hook()
    await waitFor(() => expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'step', 'migrate'))
    await waitFor(() => expect(window.localStorage.getItem(KEY)).toBeNull())
    expect(result.current.generationMode).toBe('step')
  })

  it('a fresh install shows the backend auto and writes nothing', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(ipcInvoke).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('a backend answering balanced is shown as balanced, not coerced to step', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'balanced', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('balanced'))
    expect(ipcInvoke).not.toHaveBeenCalled()
  })

  it('an unknown backend mode still falls back to step', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'plan', stored: true } })
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(result.current.generationMode).toBe('step')
  })

  it('a migration answered with balanced is shown as balanced', async () => {
    window.localStorage.setItem(KEY, 'step')
    mockedGet.mockResolvedValue({ data: { mode: 'balanced', stored: false } })
    ipcInvoke.mockResolvedValue({ mode: 'balanced', previous: 'balanced' })
    const { result } = hook()
    await waitFor(() => expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'step', 'migrate'))
    await waitFor(() => expect(result.current.generationMode).toBe('balanced'))
  })

  it('the selector writes balanced through Electron main and shows it', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: true } })
    ipcInvoke.mockResolvedValue({ mode: 'balanced', previous: 'step', approved_pending: [] })
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('balanced', 'settings') })
    expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'balanced', 'settings')
    expect(result.current.generationMode).toBe('balanced')
  })

  it('an agy refusal of balanced names balanced, not step', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: true } })
    ipcInvoke.mockResolvedValue({ refused: { code: 'agy_step_refused', message: 'x', pids: '77' } })
    const showToast = vi.fn()
    const { result } = hook(showToast)
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    await act(async () => { await result.current.setGenerationMode('balanced') })
    expect(result.current.generationMode).toBe('auto')
    expect(showToast).toHaveBeenCalledWith(cevir('mode.agyBalancedRefused', { pids: '77' }), 'error')
  })

  it('an invalid old value is not migrated', async () => {
    window.localStorage.setItem(KEY, 'plan')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(ipcInvoke).not.toHaveBeenCalled()
  })

  it('shows step and writes nothing until the backend answers', async () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    expect(result.current.generationMode).toBe('step')
    expect(ipcInvoke).not.toHaveBeenCalled()
  })

  it('the selector writes through Electron main, not localStorage', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: true } })
    ipcInvoke.mockResolvedValue({ mode: 'auto', previous: 'step' })
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('auto') })
    expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'auto', 'chat')
    expect(result.current.generationMode).toBe('auto')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('a refused write keeps the old mode and says so', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: true } })
    ipcInvoke.mockRejectedValue(new Error('reddedildi'))
    const showToast = vi.fn()
    const { result } = hook(showToast)
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('auto') })
    expect(result.current.generationMode).toBe('step')
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('reddedildi'), 'error')
  })

  it('a coded refusal keeps the old mode and shows the translated reason', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: true } })
    ipcInvoke.mockResolvedValue({ refused: { code: 'agy_step_refused', message: 'Türkçe metin', pids: '4242' } })
    const showToast = vi.fn()
    const { result } = hook(showToast)
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('step') })
    expect(result.current.generationMode).toBe('auto')
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('pid 4242'), 'error')
    expect(showToast).not.toHaveBeenCalledWith(expect.stringContaining('Türkçe metin'), 'error')
  })
})

// --- Balanced mode: cards survive the switch, critical ones say why ---

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
const makeStream = () => {
  const queue: any[] = []
  let waiter: ((v: any) => void) | null = null
  return {
    push: (ev: object) => {
      const item = { done: false, value: enc(ev) }
      if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
    },
    response: { ok: true, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise(res => { waiter = res }),
    }) } },
  }
}

describe('useChat · switching to balanced keeps the in-chat card', () => {
  let stream: ReturnType<typeof makeStream>
  const mockedPost = (axios as any).post as ReturnType<typeof vi.fn>

  beforeEach(() => {
    ipcInvoke.mockReset()
    window.localStorage.clear()
    mockedGet.mockReset().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
      if (String(url).includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
      return { data: [] }
    })
    mockedPost.mockReset().mockResolvedValue({ data: {} })
    stream = makeStream()
    vi.stubGlobal('fetch', vi.fn((url: string) =>
      String(url).endsWith('/chat-stream') ? Promise.resolve(stream.response) : new Promise(() => {})))
  })
  afterEach(() => { vi.unstubAllGlobals() })

  const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

  /** One chat with one in-chat command card; `extra` adds the balanced-mode fields. */
  const withCard = async (gateId: string, extra: object) => {
    const r = hook()
    await waitFor(() => expect(r.result.current.generationMode).toBe('step'))
    await act(async () => { await r.result.current.selectConversation({ id: 1 } as any) })
    await flush()
    act(() => { void r.result.current.sendMessage('q', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await flush()
    act(() => { stream.push({ type: 'command_approval_needed', command: 'npm install left-pad', gate_id: gateId, ...extra }) })
    await flush()
    await waitFor(() => expect(r.result.current.pendingCommand?.gateId).toBe(gateId))
    return r
  }

  it('carries risk_reason and risk_detail into the pending command', async () => {
    const { result } = await withCard('g1', { risk_reason: 'shell_installer', risk_detail: 'npm' })
    expect(result.current.pendingCommand).toMatchObject({
      command: 'npm install left-pad', gateId: 'g1', riskReason: 'shell_installer', riskDetail: 'npm',
    })
  })

  it('a card without risk fields carries none', async () => {
    const { result } = await withCard('g0', {})
    expect(result.current.pendingCommand!.riskReason).toBeUndefined()
    expect(result.current.pendingCommand!.riskDetail).toBeUndefined()
  })

  it('switching to balanced keeps the card; switching to auto still clears it', async () => {
    const { result } = await withCard('g1', { risk_reason: 'shell_installer', risk_detail: 'npm' })
    ipcInvoke.mockResolvedValue({ mode: 'balanced', previous: 'step', approved_pending: [] })
    await act(async () => { await result.current.setGenerationMode('balanced') })
    expect(result.current.generationMode).toBe('balanced')
    expect(result.current.pendingCommand?.gateId).toBe('g1')

    ipcInvoke.mockResolvedValue({ mode: 'auto', previous: 'balanced', approved_pending: ['g1'] })
    await act(async () => { await result.current.setGenerationMode('auto') })
    expect(result.current.generationMode).toBe('auto')
    expect(result.current.pendingCommand).toBeNull()
  })
})

describe('Risk reason line on approval cards', () => {
  it('the in-chat command card shows "Kritik: <label> — <detail>" when balanced raised it', () => {
    render(<CommandApproval command="npm install x" riskReason="shell_installer" riskDetail="npm" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    const line = screen.getByTestId('risk-reason')
    expect(line.textContent).toContain(translations.tr['risk.label'])
    expect(line.textContent).toContain(translations.tr['risk.shell_installer'])
    expect(line.textContent).toContain('npm')
  })

  it('a card with a risk reason keeps its own warning under the risk line (P2 dropped it)', () => {
    render(<CommandApproval command="npm install x" riskReason="shell_installer" riskDetail="npm" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    const warning = screen.getByTestId('card-warning')
    expect(warning.textContent).toContain(translations.tr['cmdApproval.warning'])
    // the reason comes first, the warning second
    expect(screen.getByTestId('risk-reason').compareDocumentPosition(warning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    cleanup()
    render(<FileDeleteApproval path="Assets/a.cs" riskReason="file_delete" onConfirm={vi.fn(async () => {})} onCancel={vi.fn()} />)
    expect(screen.getByTestId('risk-reason')).toBeTruthy()
    expect(screen.getByTestId('card-warning').textContent).toContain(translations.tr['deleteApproval.warning'])
  })

  it('without a reason the warning is the only line', () => {
    render(<FileDeleteApproval path="Assets/a.cs" onConfirm={vi.fn(async () => {})} onCancel={vi.fn()} />)
    expect(screen.queryByTestId('risk-reason')).toBeNull()
    expect(screen.getAllByTestId('card-warning')).toHaveLength(1)
  })

  it('no reason, no line: auto/step cards look as before', () => {
    render(<CommandApproval command="ls" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.queryByTestId('risk-reason')).toBeNull()
  })

  it('an empty detail shows the label alone', () => {
    render(<CommandApproval command="x" kind="unity" riskReason="unity_critical_action" riskDetail="" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    const line = screen.getByTestId('risk-reason')
    expect(line.textContent).toBe(`${translations.tr['risk.label']} ${translations.tr['risk.unity_critical_action']}`)
  })

  it('an unknown code is shown raw, never hidden', () => {
    expect(riskReasonLabel('tr', 'brand_new_code')).toBe('brand_new_code')
    expect(riskReasonLabel('en', 'toString')).toBe('toString')
    render(<CommandApproval command="x" riskReason="brand_new_code" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByTestId('risk-reason').textContent).toContain('brand_new_code')
  })

  it('every backend reason code has a label in both languages', () => {
    const codes = ['shell_unknown_program', 'shell_delete_move', 'shell_installer', 'shell_network',
      'shell_git_write', 'shell_inline_code', 'shell_metachar', 'shell_outside_workspace', 'shell_unparseable',
      'file_protected', 'file_delete', 'file_move', 'file_outside_workspace', 'read_outside_workspace',
      'unity_critical_action', 'unity_unknown_tool', 'permission_request', 'unknown_action']
    for (const lang of ['tr', 'en'] as const) {
      for (const code of codes) expect(riskReasonLabel(lang, code)).not.toBe(code)
    }
    expect(translations.tr['risk.label']).toBe('Kritik:')
    expect(translations.en['risk.label']).toBe('Critical:')
  })

  it('gateRisk reads only the top-level fields of a /mcp-pending entry', () => {
    expect(gateRisk({ tool: 'bash', risk_reason: 'shell_network', risk_detail: 'curl' }))
      .toEqual({ riskReason: 'shell_network', riskDetail: 'curl' })
    expect(gateRisk({ tool: 'bash', risk_reason: 'shell_network' })).toEqual({ riskReason: 'shell_network', riskDetail: '' })
    expect(gateRisk({ tool: 'bash', params: { risk_reason: 'shell_network' } })).toEqual({})
    expect(gateRisk({ tool: 'bash', risk_reason: '' })).toEqual({})
    expect(gateRisk(null)).toEqual({})
  })

  const cardProps = (over: object) => ({
    workspaceMismatch: false, openWorkspacePath: '/ws', onResolved: vi.fn(),
    apiBase: 'http://127.0.0.1:8000', sessionToken: 'tok', showToast: vi.fn(), refreshFileTree: vi.fn(),
    pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingDelete: null, setPendingDelete: vi.fn(),
    pendingCommand: null, setPendingCommand: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    ...over,
  }) as any

  it('the MCP command card shows the reason line from its gate', () => {
    render(<McpApprovalCards {...cardProps({
      gate: { gateId: 'm1', tool: 'bash', workspacePath: '/ws', riskReason: 'shell_unknown_program', riskDetail: 'foo.exe' },
      pendingCommand: { command: 'foo.exe --x', gateId: 'm1', messageId: MCP_MSG_ID },
    })} />)
    const line = screen.getByTestId('risk-reason')
    expect(line.textContent).toContain(translations.tr['risk.label'])
    expect(line.textContent).toContain(translations.tr['risk.shell_unknown_program'])
    expect(line.textContent).toContain('foo.exe')
  })

  it('the MCP delete card shows it too; a gate without a reason shows none', () => {
    render(<McpApprovalCards {...cardProps({
      gate: { gateId: 'm2', tool: 'delete_file', workspacePath: '/ws', riskReason: 'file_delete', riskDetail: 'Assets/a.cs' },
      pendingDelete: { path: 'Assets/a.cs', messageId: MCP_MSG_ID },
    })} />)
    expect(screen.getByTestId('risk-reason').textContent).toContain(translations.tr['risk.file_delete'])
    cleanup()
    render(<McpApprovalCards {...cardProps({
      gate: { gateId: 'm3', tool: 'bash', workspacePath: '/ws' },
      pendingCommand: { command: 'ls', gateId: 'm3', messageId: MCP_MSG_ID },
    })} />)
    expect(screen.queryByTestId('risk-reason')).toBeNull()
  })

  it('useMCPApproval puts a polled entry\'s top-level risk fields on the open gate', async () => {
    const pending = { g9: { tool: 'manage_editor', params: { action: 'set_player_settings' }, workspace_path: '/ws',
      risk_reason: 'unity_critical_action', risk_detail: 'manage_editor.set_player_settings' } }
    mockedGet.mockReset().mockResolvedValue({ data: { pending } })
    const setPendingCommand = vi.fn()
    const { result } = renderHook(() => useMCPApproval({
      API: 'http://127.0.0.1:8000', enabled: false, workspacePath: '/ws',
      setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand, setPendingFix: vi.fn(),
    }))
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toMatchObject({
      gateId: 'g9', riskReason: 'unity_critical_action', riskDetail: 'manage_editor.set_player_settings',
    })
    expect(setPendingCommand).toHaveBeenCalledWith(expect.objectContaining({ gateId: 'g9', kind: 'unity' }))
  })

  it('an open MCP card picks up a risk reason added after it opened', async () => {
    const entry: any = { tool: 'bash', params: { command: 'rm -r x' }, workspace_path: '/ws' }
    mockedGet.mockReset().mockImplementation(async () => ({ data: { pending: { g7: entry } } }))
    const { result } = renderHook(() => useMCPApproval({
      API: 'http://127.0.0.1:8000', enabled: false, workspacePath: '/ws',
      setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand: vi.fn(), setPendingFix: vi.fn(),
    }))
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate?.gateId).toBe('g7')
    expect(result.current.activeGate?.riskReason).toBeUndefined()
    entry.risk_reason = 'shell_delete_move'
    entry.risk_detail = 'rm'
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toMatchObject({ gateId: 'g7', riskReason: 'shell_delete_move', riskDetail: 'rm' })
  })
})

describe('Electron main accepts balanced', () => {
  it('the approval-mode-set handler lets balanced through', () => {
    const main = readFileSync(resolve(__dirname, '../main/background.ts'), 'utf8')
    expect(main).toMatch(/mode !== 'auto' && mode !== 'balanced' && mode !== 'step'/)
  })
})
