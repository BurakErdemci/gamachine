import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { default: { get, post, delete: vi.fn(), put: vi.fn() }, get, post }
})

import axios from 'axios'
import { BlenderApproval, blenderCardInfo } from '../renderer/components/home/BlenderApproval'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { useMCPApproval } from '../renderer/hooks/home/useMCPApproval'
import { aktifDilAyarla, ceviriUygula } from '../renderer/lib/i18n'

const mockedGet = (axios as any).get as ReturnType<typeof vi.fn>
const API = 'http://127.0.0.1:8000'

const CODE = "import os\nos.remove('C:/x')"
const PARAMS = {
  code: CODE, code_chars: CODE.length, truncated: false,
  reason: 'blender_code: system module (os)', connector: 'blender', mode: 'balanced',
}

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  aktifDilAyarla('tr')
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('BlenderApproval card', () => {
  it('shows the reason and the code in a scrollable block', () => {
    render(<BlenderApproval info={blenderCardInfo(PARAMS)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bl-card-risk').textContent).toContain(PARAMS.reason)
    const code = screen.getByTestId('bl-card-code')
    expect(code.tagName).toBe('PRE')
    expect(code.className).toContain('custom-scrollbar')
    expect(code.textContent).toBe(CODE)
    expect(screen.queryByTestId('bl-card-truncated')).toBeNull()
  })

  it('strips bidi controls from the code and the reason', () => {
    const info = blenderCardInfo({ ...PARAMS, code: 'x = 1  # \u202Eevil', reason: 'r\u2066eason' })
    render(<BlenderApproval info={info} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bl-card-code').textContent).toBe('x = 1  # evil')
    expect(screen.getByTestId('bl-card-risk').textContent).toContain('reason')
    expect(screen.getByTestId('bl-card-risk').textContent).not.toContain('\u2066')
  })

  it('says the code was cut for display', () => {
    const info = blenderCardInfo({ ...PARAMS, code: 'x', code_chars: 30000, truncated: true })
    render(<BlenderApproval info={info} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bl-card-truncated').textContent)
      .toBe(ceviriUygula('tr', 'bl.card.truncated', { shown: 1, total: 30000 }))
  })

  it('explains a step-mode card that has no trigger', () => {
    render(<BlenderApproval info={blenderCardInfo({ ...PARAMS, reason: '', mode: 'step' })} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bl-card-risk').textContent).toContain(ceviriUygula('tr', 'bl.card.stepReason'))
  })

  it('answers approve and reject, with no remember option', () => {
    const onAnswer = vi.fn()
    render(<BlenderApproval info={blenderCardInfo(PARAMS)} onAnswer={onAnswer} />)
    fireEvent.click(screen.getByTestId('bl-card-approve'))
    expect(onAnswer).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByTestId('bl-card-reject'))
    expect(onAnswer).toHaveBeenLastCalledWith(false)
    expect(screen.queryByText(/hatırla|remember|always/i)).toBeNull()
  })

  it('has every label in both languages', () => {
    for (const key of ['bl.card.who', 'bl.card.name', 'bl.card.sentence', 'bl.card.riskUnknown',
      'bl.card.stepReason', 'bl.card.truncated']) {
      expect(ceviriUygula('tr', key)).not.toBe(key)
      expect(ceviriUygula('en', key)).not.toBe(key)
    }
    expect(ceviriUygula('en', 'bl.card.sentence')).not.toBe(ceviriUygula('tr', 'bl.card.sentence'))
  })

  it('reads malformed params safely', () => {
    expect(blenderCardInfo(null)).toEqual({ code: '', codeChars: 0, truncated: false, reason: '', mode: '' })
    expect(blenderCardInfo({ code: 'abc', code_chars: 1 }).codeChars).toBe(3)
  })
})

describe('blender_execute_code through the MCP approval flow', () => {
  const hook = () => renderHook(() => useMCPApproval({
    API, enabled: false, workspacePath: '/ws', screenConvId: 5,
    setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand: vi.fn(), setPendingFix: vi.fn(),
  } as any))

  it('the poll routes the card to its own slot, not the generic command card', async () => {
    mockedGet.mockReset().mockResolvedValue({ data: { pending: { 'blender-1': {
      tool: 'blender_execute_code', params: PARAMS, workspace_path: '', conversation_id: 5,
      risk_reason: PARAMS.reason, summary: 'blender code: import os' } } } })
    const { result } = hook()
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toMatchObject({
      gateId: 'blender-1', tool: 'blender_execute_code', conversationId: 5,
      blender: { code: CODE, reason: PARAMS.reason, mode: 'balanced', truncated: false },
    })
  })

  it.each([true, false])('McpApprovalCards posts the answer (%s) to the MCP respond route', async (approved) => {
    const props = {
      gate: { gateId: 'blender-1', tool: 'blender_execute_code', workspacePath: '', conversationId: 5,
        blender: blenderCardInfo(PARAMS) },
      workspaceMismatch: false, openWorkspacePath: '/ws', onResolved: vi.fn(),
      apiBase: API, sessionToken: 'tok', showToast: vi.fn(), refreshFileTree: vi.fn(),
      pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingDelete: null, setPendingDelete: vi.fn(),
      pendingCommand: null, setPendingCommand: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    } as any
    render(<McpApprovalCards {...props} />)
    expect(screen.getByTestId('bl-card-code').textContent).toBe(CODE)
    await act(async () => { fireEvent.click(screen.getByTestId(approved ? 'bl-card-approve' : 'bl-card-reject')) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as any
    expect(url).toBe(`${API}/mcp-approval-respond/blender-1`)
    expect(JSON.parse(init.body)).toEqual({ approved })
    expect(props.onResolved).toHaveBeenCalledTimes(1)
  })
})
