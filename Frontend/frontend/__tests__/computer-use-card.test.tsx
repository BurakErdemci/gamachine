/**
 * computer_* approval cards: routed by useMCPApproval to their own card (not the generic
 * 'unity' command card), drawn on the shared ApprovalCard chrome, and answered through the
 * `computer-use` channel (`answer-card` with `remember`), or, outside Electron, through the
 * plain MCP answer with `approved` only.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, renderHook, act, waitFor } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { default: { get, post, delete: vi.fn(), put: vi.fn() }, get, post }
})

import axios from 'axios'
import { useMCPApproval, MCP_MSG_ID } from '../renderer/hooks/home/useMCPApproval'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { computerCardInfo } from '../renderer/components/home/ComputerUseApproval'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const mockedGet = (axios as any).get as ReturnType<typeof vi.fn>
const API = 'http://127.0.0.1:8000'
const PARAMS = { bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, risky: false,
  risk_reason: '', summary: 'Type the title into the open document', foreground: false }

let invoke: ReturnType<typeof vi.fn>
beforeEach(() => {
  aktifDilAyarla('tr')
  invoke = vi.fn(async () => ({ status: 'ok' }))
  ;(window as any).ipc = { invoke }
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.unstubAllGlobals()
})

const hook = (setPendingCommand = vi.fn()) => renderHook(() => useMCPApproval({
  API, enabled: false, workspacePath: '/ws', screenConvId: 5,
  setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand, setPendingFix: vi.fn(),
}))

describe('useMCPApproval · computer_* routing', () => {
  it('a computer_* entry opens the computer card with its structured params', async () => {
    mockedGet.mockReset().mockResolvedValue({ data: { pending: { cu1: {
      tool: 'computer_click', params: PARAMS, workspace_path: '', conversation_id: 5,
      computer_use: true, risk_reason: 'Bu uygulamaya ilk erişim için onay gerekiyor.', summary: PARAMS.summary,
    } } } })
    const setPendingCommand = vi.fn()
    const { result } = hook(setPendingCommand)
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toMatchObject({ gateId: 'cu1', tool: 'computer_click', conversationId: 5,
      computer: { tool: 'computer_click', bundleId: 'com.apple.TextEdit', appName: 'TextEdit', pid: 4242,
        risky: false, summary: PARAMS.summary, foreground: false } })
    expect(setPendingCommand).not.toHaveBeenCalled()
    // A later poll keeps the structured params on the open gate.
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate?.computer?.appName).toBe('TextEdit')
  })

  it('a non-computer unknown tool still goes to the generic unity card', async () => {
    mockedGet.mockReset().mockResolvedValue({ data: { pending: { g1: {
      tool: 'manage_scene', params: { action: 'save' }, workspace_path: '/ws', conversation_id: 5 } } } })
    const setPendingCommand = vi.fn()
    const { result } = hook(setPendingCommand)
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate?.computer).toBeUndefined()
    expect(setPendingCommand).toHaveBeenCalledWith(expect.objectContaining({ gateId: 'g1', kind: 'unity', messageId: MCP_MSG_ID }))
  })

  it('a computer card of another chat is not drawn here', async () => {
    mockedGet.mockReset().mockResolvedValue({ data: { pending: { cu2: {
      tool: 'computer_click', params: PARAMS, workspace_path: '', conversation_id: 9, computer_use: true } } } })
    const { result } = hook()
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toBeNull()
  })
})

const cardProps = (gate: any, over: any = {}) => ({
  gate, workspaceMismatch: false, openWorkspacePath: '/ws', onResolved: vi.fn(),
  apiBase: API, sessionToken: 'tok', showToast: vi.fn(), refreshFileTree: vi.fn(),
  pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingDelete: null, setPendingDelete: vi.fn(),
  pendingCommand: null, setPendingCommand: vi.fn(), pendingFix: null, setPendingFix: vi.fn(), ...over,
}) as any

const gateFor = (tool: string, params: any = PARAMS, gateId = 'cu-1') =>
  ({ gateId, tool, workspacePath: '', conversationId: 5, computer: computerCardInfo(tool, params) })

describe('computer card · first use', () => {
  it('names the app and what will happen, with three answers and no command card', () => {
    render(<McpApprovalCards {...cardProps(gateFor('computer_click'))} />)
    const card = screen.getByTestId('computer-approval')
    expect(card.getAttribute('data-kind')).toBe('computer')
    expect(card.textContent).toContain(cevir('cu.card.nameFirst', { ad: 'TextEdit' }))
    expect(screen.getByTestId('cu-card-summary').textContent).toBe(PARAMS.summary)
    expect(screen.getByTestId('cu-card-app').textContent).toContain('com.apple.TextEdit')
    expect(screen.getByTestId('cu-card-once').textContent).toBe(cevir('cu.card.once'))
    expect(screen.getByTestId('cu-card-always').textContent).toBe(cevir('cu.card.always'))
    expect(screen.getByTestId('cu-card-reject').textContent).toBe(cevir('cu.card.reject'))
    expect(screen.queryByTestId('command-approval')).toBeNull()
    expect(screen.queryByTestId('cu-card-editor')).toBeNull()
    expect(screen.queryByTestId('cu-card-risk')).toBeNull()
    // No project strip: a computer request belongs to no workspace.
    expect(document.querySelector('.approval-src')).toBeNull()
  })

  it.each([
    ['cu-card-once', true, false, 'cu.card.sent'],
    ['cu-card-always', true, true, 'cu.card.sentAlways'],
    ['cu-card-reject', false, false, 'cu.card.rejected'],
  ] as const)('%s answers through the computer-use channel', async (button, approved, remember, toastKey) => {
    const props = cardProps(gateFor('computer_click'))
    render(<McpApprovalCards {...props} />)
    await act(async () => { fireEvent.click(screen.getByTestId(button)) })
    expect(invoke).toHaveBeenCalledExactlyOnceWith('computer-use', { op: 'answer-card', gateId: 'cu-1', approved, remember })
    expect(props.onResolved).toHaveBeenCalledTimes(1)
    expect(props.showToast).toHaveBeenCalledWith(cevir(toastKey, { ad: 'TextEdit' }), 'info')
  })

  it('never posts to the backend directly while Electron is there', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<McpApprovalCards {...cardProps(gateFor('computer_click'))} />)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-always')) })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('a second press while the first answer is in flight is suppressed and said so', async () => {
    let release!: (v: unknown) => void
    invoke.mockImplementation(() => new Promise(r => { release = r }))
    const props = cardProps(gateFor('computer_click'))
    render(<McpApprovalCards {...props} />)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-once')) })
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-reject')) })
    expect(invoke).toHaveBeenCalledTimes(1)
    expect(props.showToast).toHaveBeenCalledWith(cevir('mcp.suppressedInFlight'), 'warning')
    await act(async () => { release({ status: 'ok' }) })
    expect(props.showToast).toHaveBeenCalledWith(cevir('cu.card.sent'), 'info')
  })

  it('a refused or failed answer is reported, not shown as sent', async () => {
    invoke.mockResolvedValueOnce({ status: 'gate_expired', error: 'x' })
    const props = cardProps(gateFor('computer_click'))
    render(<McpApprovalCards {...props} />)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-once')) })
    expect(props.showToast).toHaveBeenCalledWith(cevir('gate.deliver.expired', { etiket: cevir('gate.label.mcp') }), 'error')
    cleanup()
    invoke.mockResolvedValueOnce({ error: 'Computer-use request failed.' })
    const again = cardProps(gateFor('computer_click', PARAMS, 'cu-2'))
    render(<McpApprovalCards {...again} />)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-once')) })
    const [message, type] = again.showToast.mock.calls[0]
    expect(type).toBe('warning')
    expect(message).not.toBe(cevir('cu.card.sent'))
  })

  it('a code editor card says its terminal runs commands without a card', () => {
    render(<McpApprovalCards {...cardProps(gateFor('computer_type_text',
      { ...PARAMS, bundle_id: 'com.microsoft.VSCode', app_name: 'Visual Studio Code', foreground: true }))} />)
    expect(screen.getByTestId('cu-card-editor').textContent).toBe(cevir('cu.card.editor'))
    expect(screen.getByTestId('cu-card-foreground').textContent).toBe(cevir('cu.card.foreground'))
  })
})

describe('computer card · risky step', () => {
  it.each([
    ['computer_confirm_risky_step', { ...PARAMS, risky: true, risk_reason: 'Send the email to 40 people' }],
    ['computer_hotkey', { ...PARAMS, risky: true, risk_reason: 'Risky keyboard shortcut: cmd+q' }],
  ])('%s offers Approve / Deny only, with the reason up front', async (tool, params) => {
    render(<McpApprovalCards {...cardProps(gateFor(tool, params))} />)
    expect(screen.getByTestId('cu-card-risk').textContent).toContain(params.risk_reason)
    expect(screen.queryByTestId('cu-card-once')).toBeNull()
    expect(screen.queryByTestId('cu-card-always')).toBeNull()
    expect(screen.getByTestId('computer-approval').textContent).toContain(cevir('cu.card.nameRisky', { ad: 'TextEdit' }))
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-approve')) })
    expect(invoke).toHaveBeenCalledExactlyOnceWith('computer-use', { op: 'answer-card', gateId: 'cu-1', approved: true, remember: false })
  })

  it('a risky step without a reason still says it is risky', () => {
    render(<McpApprovalCards {...cardProps(gateFor('computer_confirm_risky_step', { ...PARAMS, risk_reason: '' }))} />)
    expect(screen.getByTestId('cu-card-risk').textContent).toContain(cevir('cu.card.riskUnknown'))
  })

  it('deny sends approved false', async () => {
    render(<McpApprovalCards {...cardProps(gateFor('computer_confirm_risky_step', { ...PARAMS, risky: true }))} />)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-card-reject')) })
    expect(invoke).toHaveBeenCalledExactlyOnceWith('computer-use', { op: 'answer-card', gateId: 'cu-1', approved: false, remember: false })
  })
})

describe('computer card · outside Electron', () => {
  it.each([
    ['cu-card-once', true], ['cu-card-always', true], ['cu-card-reject', false],
  ] as const)('%s falls back to the MCP answer with approved only', async (button, approved) => {
    delete (window as any).ipc
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const props = cardProps(gateFor('computer_click'))
    render(<McpApprovalCards {...props} />)
    await act(async () => { fireEvent.click(screen.getByTestId(button)) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as any[]
    expect(url).toBe(`${API}/mcp-approval-respond/cu-1`)
    expect(JSON.parse(init.body)).toEqual({ approved })
    expect(init.headers['X-Session-Token']).toBe('tok')
    await waitFor(() => expect(props.onResolved).toHaveBeenCalledTimes(1))
  })
})

describe('computerCardInfo', () => {
  it('tolerates malformed params', () => {
    expect(computerCardInfo('computer_click', null)).toEqual({ tool: 'computer_click', bundleId: null, appName: null,
      pid: null, risky: false, riskReason: '', summary: '', foreground: false })
    expect(computerCardInfo('computer_click', { pid: '1', risky: 'true', summary: 7 }, { summary: 'from entry' }))
      .toMatchObject({ pid: null, risky: false, summary: 'from entry' })
  })
})
