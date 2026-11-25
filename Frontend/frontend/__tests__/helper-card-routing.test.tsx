/**
 * A helper's approval card on the parent chat (4.1.0 design D3). Before: /mcp-pending entries of
 * a helper named the helper's own chat, which is never on screen, so the card reached neither the
 * parent's slot nor the tray and the helper waited in silence. Now an entry carrying
 * parent_conversation_id is drawn in the parent while the parent is on screen, says who asks, and
 * links to the helper's conversation; ordinary entries keep their routing.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn(); const post = vi.fn()
  return { default: { get, post, delete: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } }, get, post }
})
import axios from 'axios'
import { useMCPApproval, gateHelper, gateRoute, MCP_MSG_ID } from '../renderer/hooks/home/useMCPApproval'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { ApprovalCard, ApprovalSourceContext } from '../renderer/components/home/ApprovalCard'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const mocked = axios as unknown as { get: ReturnType<typeof vi.fn> }
const API = 'http://x'

const HELPER_ENTRY = {
  tool: 'manage_gameobject', params: { action: 'delete', target: 'Enemy_12_old' }, workspace_path: '',
  conversation_id: 40, parent_conversation_id: 12, agent_label: 'tester (Codex GPT-6.2 Sol)',
}
const OWN_ENTRY = { tool: 'manage_gameobject', params: { action: 'delete', target: 'Own' }, workspace_path: '', conversation_id: 12 }

const hook = (screenConvId: number, pending: Record<string, any>) => {
  mocked.get.mockResolvedValue({ data: { pending } })
  const owners: Record<number, string[]>[] = []
  const asking: number[][] = []
  const setPendingCommand = vi.fn()
  const view = renderHook(() => useMCPApproval({
    API, enabled: false, workspacePath: null as any,
    setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand, setPendingFix: vi.fn(),
    screenConvId, onOwnersChange: g => owners.push(g), onHelperAsking: ids => asking.push(ids),
  }))
  return { ...view, owners, asking, setPendingCommand }
}

beforeEach(() => aktifDilAyarla('en'))
afterEach(() => { cleanup(); mocked.get.mockReset(); aktifDilAyarla('tr') })

describe('routing a helper card', () => {
  it('reads the helper fields and routes to the parent', () => {
    expect(gateHelper(HELPER_ENTRY)).toEqual({ chatId: 40, parentId: 12, label: 'tester (Codex GPT-6.2 Sol)' })
    expect(gateRoute(HELPER_ENTRY)).toBe(12)
    expect(gateHelper(OWN_ENTRY)).toBeNull()
    expect(gateRoute(OWN_ENTRY)).toBe(12)
  })

  it('is drawn in the parent chat on screen, marks the parent row, and names the helper as asking', async () => {
    const { result, owners, asking, setPendingCommand } = hook(12, { g1: HELPER_ENTRY })
    await act(async () => { await result.current.poll() })
    expect(owners.at(-1)).toEqual({ 12: ['g1'] })
    expect(asking.at(-1)).toEqual([40])
    expect(result.current.activeGate).toMatchObject({ gateId: 'g1', conversationId: 12, helper: { chatId: 40, parentId: 12 } })
    expect(setPendingCommand).toHaveBeenCalledWith(expect.objectContaining({ gateId: 'g1', messageId: MCP_MSG_ID, kind: 'unity' }))
    // never the tray
    expect(result.current.unknownGates).toEqual([])
  })

  it('waits off screen while another chat is open, like any owned card', async () => {
    const { result, owners } = hook(77, { g1: HELPER_ENTRY })
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toBeNull()
    expect(owners.at(-1)).toEqual({ 12: ['g1'] })
  })

  it('leaves the chat\'s own cards as they were', async () => {
    const { result, asking } = hook(12, { g2: OWN_ENTRY })
    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).toMatchObject({ gateId: 'g2', conversationId: 12 })
    expect(result.current.activeGate?.helper).toBeUndefined()
    expect(asking).toEqual([])
  })
})

describe('the card on screen', () => {
  const draw = (gate: any, onOpenHelper = vi.fn()) => {
    render(
      <McpApprovalCards
        gate={gate} workspaceMismatch={false} openWorkspacePath={null} onResolved={() => {}}
        apiBase={API} sessionToken="t" showToast={() => {}} refreshFileTree={() => {}}
        pendingGenFiles={null} setPendingGenFiles={() => {}} pendingDelete={null} setPendingDelete={() => {}}
        pendingCommand={{ command: 'delete Enemy_12_old', gateId: gate.gateId, messageId: MCP_MSG_ID, kind: 'unity' }}
        setPendingCommand={() => {}} pendingFix={null} setPendingFix={() => {}}
        onOpenHelper={onOpenHelper}
      />,
    )
    return onOpenHelper
  }

  it('says "<name> (<model>) asks" and links to the helper\'s conversation', () => {
    const open = draw({ gateId: 'g1', tool: 'manage_gameobject', workspacePath: '', conversationId: 12, helper: gateHelper(HELPER_ENTRY) })
    expect(screen.getByTestId('approval-source').textContent).toBe('tester (Codex GPT-6.2 Sol) asks')
    fireEvent.click(screen.getByTestId('approval-open-helper'))
    expect(open).toHaveBeenCalledWith({ chatId: 40 })
    expect(screen.getByTestId('approval-open-helper').textContent).toBe('Open tester’s conversation')
  })

  it('a call inside a script says where it came from', () => {
    draw({ gateId: 'g3', tool: 'manage_gameobject', workspacePath: '', conversationId: 12, scriptCall: 13 })
    expect(screen.getByTestId('approval-source').textContent).toBe('From script · call #13')
  })

  it('an ordinary card keeps its own source cell', () => {
    render(<ApprovalCard kind="unity" who="Unity" name="x" />)
    expect(screen.queryByTestId('approval-source')).toBeNull()
    cleanup()
    render(<ApprovalSourceContext.Provider value={{ who: 'tester asks' }}><ApprovalCard kind="unity" who="Unity" name="x" /></ApprovalSourceContext.Provider>)
    expect(screen.getByTestId('approval-source').textContent).toBe('tester asks')
  })
})
