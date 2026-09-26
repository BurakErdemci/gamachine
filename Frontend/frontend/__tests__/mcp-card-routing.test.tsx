/**
 * PARALLEL CHATS (Phase 3, slice 2): Unity MCP approval cards go to the chat
 * that owns them.
 *
 * `/mcp-pending` now names an owner per request: a chat id, or `null` when
 * the backend cannot tell. Before this slice every such card showed in
 * whichever chat was on screen, so chat A's Unity action was offered for
 * approval inside chat B.
 *
 * The harness wires the real `useChat`, `useMCPApproval`, `Sidebar`,
 * `ChatPanel` and tray together the way `home.tsx` does; `home.tsx` itself
 * cannot be mounted here (Electron IPC, auth, Monaco). `/mcp-pending` is
 * served from `pending`, which each test rewrites and then polls.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, act, within, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval, gateOwner } from '../renderer/hooks/home/useMCPApproval'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { McpUnknownTray } from '../renderer/components/home/McpUnknownTray'
import { cevir, translations } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const CONVS = [{ id: 1, title: 'one' }, { id: 2, title: 'two' }] as any[]

/** A Unity request whose summary carries a marker unique to the test. */
const req = (marker: string, owner?: number | null | string) => ({
  tool: 'manage_gameobject',
  params: { action: 'delete', target: marker, unity_instance: 'Game@abc' },
  workspace_path: '/ws',
  ...(owner === undefined ? {} : { conversation_id: owner }),
})

let pending: Record<string, any>
let decisions: Array<{ gateId: string; approved: boolean }>
let api: { chat: ReturnType<typeof useChat>; mcp: ReturnType<typeof useMCPApproval> }
let renders: Array<{ screen: number | null; gate: string | null }>

// Stable across renders: fresh functions here would rebuild `sendMessage`
// every render and reopen the wake channel each time.
const showToast = vi.fn()
const refreshFileTree = vi.fn()
const suggest = (n: string) => n

const Harness: React.FC = () => {
  const [genFiles, setGenFiles] = React.useState<any>(null)
  const [del, setDel] = React.useState<any>(null)
  const chat = useChat(API, USER, CONFIG, '/ws', showToast, refreshFileTree, suggest)
  const mcp = useMCPApproval({
    API, enabled: true, workspacePath: '/ws',
    setPendingGenFiles: setGenFiles, setPendingDelete: setDel,
    setPendingCommand: chat.setPendingCommand, setPendingFix: chat.setPendingFix,
    showToast, screenConvId: chat.activeConvId, onOwnersChange: chat.setBridgeGates,
  })
  api = { chat, mcp }
  renders.push({ screen: chat.activeConvId, gate: mcp.activeGate?.gateId ?? null })
  return (
    <>
      <McpUnknownTray gates={mcp.unknownGates} apiBase={API} sessionToken="tok" showToast={showToast} />
      <Sidebar
        {...({} as any)}
        isSidebarOpen sidebarTab="chats" setSidebarTab={vi.fn()}
        conversations={CONVS} activeConvId={chat.activeConvId} convStatus={chat.convStatus}
        selectConversation={chat.selectConversation}
        createNewConversation={vi.fn()} deleteConversation={vi.fn()}
        editingId={null} setEditingId={vi.fn()} tempTitle="" setTempTitle={vi.fn()} saveRename={vi.fn()}
        user={USER} setShowSettings={vi.fn()} handleLogout={vi.fn()}
      />
      <div data-testid="chat-panel">
        <ChatPanel {...({
          messages: chat.messages, activeConvId: chat.activeConvId, user: USER, loading: chat.loading,
          clearHistory: vi.fn(), lang: 'tr', effectiveProvider: 'claude', thinkingLevel: 'auto',
          workspacePath: '/ws', handleExportToUnity: vi.fn(),
          pendingGenFiles: genFiles, setPendingGenFiles: setGenFiles,
          pendingFix: chat.pendingFix, setPendingFix: chat.setPendingFix,
          pendingDelete: del, setPendingDelete: setDel,
          pendingCommand: chat.pendingCommand, setPendingCommand: chat.setPendingCommand,
          openedFilePath: null, setCode: vi.fn(), refreshFileTree, analyzeProject: vi.fn(),
          openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
          ipc: { invoke: vi.fn() }, showToast, diffFile: null, setDiffFile: vi.fn(),
          onApproveCommand: chat.approveCommand, pendingQuestion: chat.pendingQuestion,
          setPendingQuestion: chat.setPendingQuestion, onAnswerQuestion: chat.answerQuestion,
          deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
          mcpGate: mcp.activeGate, mcpWorkspaceMismatch: mcp.gateWorkspaceMismatch,
          mcpWorkspaceCheckPending: mcp.gateWorkspaceCheckPending,
          mcpOpenWorkspacePath: mcp.openWorkspacePath, onMcpResolved: mcp.resolveActiveGate,
        } as any)} />
      </div>
    </>
  )
}

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }
const poll = async () => { await act(async () => { await api.mcp.poll() }); await flush() }
const open = async (id: number) => {
  await act(async () => { await api.chat.selectConversation({ id } as any) })
  await flush()
}
const mount = async () => { render(<Harness />); await flush() }

const chatPanel = () => within(screen.getByTestId('chat-panel'))
const inChat = (marker: string) => chatPanel().queryAllByText(new RegExp(marker)).length
const inTray = (marker: string) => {
  const tray = screen.queryByTestId('mcp-unknown-tray')
  return tray ? within(tray).queryAllByText(new RegExp(marker)).length : 0
}
const everywhere = (marker: string) => screen.queryAllByText(new RegExp(marker)).length
const rowStatus = (id: number) => screen.queryByTestId(`conv-status-${id}`)?.getAttribute('title') ?? null

beforeEach(() => {
  pending = {}
  decisions = []
  renders = []
  showToast.mockReset()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/mcp-pending')) return { data: { pending } }
    if (u.endsWith('/messages')) return { data: [] }
    if (u.includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
    return { data: {} }
  })
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const m = String(url).match(/\/mcp-approval-respond\/(.+)$/)
    if (m) {
      decisions.push({ gateId: m[1], approved: JSON.parse(init.body).approved })
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
    }
    return new Promise(() => {})
  }))
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('owned card · the owner chat is on screen', () => {
  it('shows in that chat, not in the tray', async () => {
    await mount()
    await open(1)
    pending = { g1: req('Mark-A1', 1) }
    await poll()
    expect(inChat('Mark-A1')).toBe(1)
    expect(screen.queryByTestId('mcp-unknown-tray')).toBeNull()
    expect(rowStatus(1)).toBe(cevir('sidebar.statusAwaiting'))
  })

  it('a card that was waiting for its chat appears the moment the chat is opened', async () => {
    pending = { g1: req('Mark-A1', 1) }
    await mount()
    expect(everywhere('Mark-A1')).toBe(0)
    await open(1)
    // No manual poll: switching polls by itself.
    expect(inChat('Mark-A1')).toBe(1)
  })
})

describe('owned card · another chat owns it', () => {
  it('stays out of the open chat, marks its row, and is shown and decidable after switching', async () => {
    await mount()
    await open(1)
    pending = { g2: req('Mark-B2', 2) }
    await poll()

    expect(everywhere('Mark-B2')).toBe(0)
    expect(rowStatus(2)).toBe(cevir('sidebar.statusAwaiting'))
    expect(rowStatus(1)).toBeNull()

    await open(2)
    expect(inChat('Mark-B2')).toBe(1)
    expect(decisions).toEqual([])

    await act(async () => { fireEvent.click(chatPanel().getByText(cevir('unityApproval.run'))) })
    await flush()
    expect(decisions).toEqual([{ gateId: 'g2', approved: true }])

    // The backend drops a decided request; the next poll clears the row.
    pending = {}
    await poll()
    expect(everywhere('Mark-B2')).toBe(0)
    expect(rowStatus(2)).toBeNull()
  })

  it('leaving the owner chat takes the card off screen undecided; returning brings it back', async () => {
    await mount()
    await open(2)
    pending = { g2: req('Mark-B2', 2) }
    await poll()
    expect(inChat('Mark-B2')).toBe(1)

    await open(1)
    expect(everywhere('Mark-B2')).toBe(0)
    expect(rowStatus(2)).toBe(cevir('sidebar.statusAwaiting'))

    await open(2)
    expect(inChat('Mark-B2')).toBe(1)
    expect(decisions).toEqual([])
  })

  it('is never handed to the screen in a render where another chat is open', async () => {
    await mount()
    await open(2)
    pending = { g2: req('Mark-B2', 2) }
    await poll()
    await open(1)
    await open(2)
    await open(1)
    const leaked = renders.filter(r => r.gate === 'g2' && r.screen !== 2)
    expect(leaked).toEqual([])
    expect(renders.some(r => r.gate === 'g2' && r.screen === 2)).toBe(true)
  })
})

describe('unknown owner · the tray', () => {
  it('goes to the tray, labelled, and never into the chat or a sidebar row', async () => {
    await mount()
    await open(1)
    pending = { g3: req('Mark-C3', null) }
    await poll()

    const tray = screen.getByTestId('mcp-unknown-tray')
    expect(within(tray).getByText(cevir('mcp.trayTitle'))).toBeTruthy()
    expect(within(tray).getByText('manage_gameobject')).toBeTruthy()
    expect(within(tray).getByText('Game@abc')).toBeTruthy()
    expect(inTray('Mark-C3')).toBe(1)
    expect(inChat('Mark-C3')).toBe(0)
    expect(api.chat.convStatus).toEqual({})

    await open(2)
    expect(inTray('Mark-C3')).toBe(1)
    expect(inChat('Mark-C3')).toBe(0)
  })

  it('queues several requests', async () => {
    await mount()
    await open(1)
    pending = { g3: req('Mark-C3', null), g4: req('Mark-C4', null) }
    await poll()
    expect(screen.getByTestId('mcp-tray-g3')).toBeTruthy()
    expect(screen.getByTestId('mcp-tray-g4')).toBeTruthy()
    expect(screen.getByText(cevir('mcp.trayCount', { sayi: 2 }))).toBeTruthy()
  })

  it('approve and deny post to /mcp-approval-respond, and only on a click', async () => {
    await mount()
    await open(1)
    pending = { g3: req('Mark-C3', null), g4: req('Mark-C4', null) }
    await poll()
    await poll()
    // Step-mode red line: polling alone never decides anything.
    expect(decisions).toEqual([])

    await act(async () => { fireEvent.click(within(screen.getByTestId('mcp-tray-g3')).getByText(cevir('mcp.trayApprove'))) })
    await flush()
    await act(async () => { fireEvent.click(within(screen.getByTestId('mcp-tray-g4')).getByText(cevir('mcp.trayDeny'))) })
    await flush()

    expect(decisions).toEqual([{ gateId: 'g3', approved: true }, { gateId: 'g4', approved: false }])
    expect(fetch).toHaveBeenCalledWith(`${API}/mcp-approval-respond/g3`, expect.objectContaining({ method: 'POST' }))
    // Delivered decisions leave the tray without waiting for the poll.
    expect(screen.queryByTestId('mcp-unknown-tray')).toBeNull()
  })

  it('a failed delivery keeps the request decidable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'gate_not_found' }) })))
    await mount()
    await open(1)
    pending = { g3: req('Mark-C3', null) }
    await poll()
    await act(async () => { fireEvent.click(screen.getByText(cevir('mcp.trayApprove'))) })
    await flush()
    const [message, type] = showToast.mock.calls.at(-1)!
    expect(message).not.toBe(cevir('mcp.trayApproved'))
    expect(type).not.toBe('info')
    expect(screen.getByTestId('mcp-tray-g3')).toBeTruthy()
  })
})

describe('a request that leaves /mcp-pending', () => {
  it('disappears from the chat, the sidebar row and the tray', async () => {
    await mount()
    await open(1)
    pending = { g5: req('Mark-A5', 1), g6: req('Mark-B6', 2), g7: req('Mark-C7', null) }
    await poll()
    expect(inChat('Mark-A5')).toBe(1)
    expect(rowStatus(2)).toBe(cevir('sidebar.statusAwaiting'))
    expect(inTray('Mark-C7')).toBe(1)

    // Resolved, withdrawn, or denied by Stop: the backend no longer lists them.
    pending = {}
    await poll()
    expect(everywhere('Mark-A5')).toBe(0)
    expect(everywhere('Mark-C7')).toBe(0)
    expect(rowStatus(1)).toBeNull()
    expect(rowStatus(2)).toBeNull()
    expect(screen.queryByTestId('mcp-unknown-tray')).toBeNull()

    await open(2)
    expect(everywhere('Mark-B6')).toBe(0)
  })
})

describe('no request is drawn twice', () => {
  it('each marker appears at most once, in the one place it belongs, across switches', async () => {
    await mount()
    await open(1)
    pending = { a: req('Mark-A', 1), b: req('Mark-B', 2), c: req('Mark-C', null) }
    await poll()

    const expectPlaces = (onScreen: 1 | 2) => {
      expect(everywhere('Mark-C')).toBe(1)
      expect(inTray('Mark-C')).toBe(1)
      expect(everywhere('Mark-A')).toBe(onScreen === 1 ? 1 : 0)
      expect(everywhere('Mark-B')).toBe(onScreen === 2 ? 1 : 0)
      expect(inTray('Mark-A') + inTray('Mark-B')).toBe(0)
    }
    expectPlaces(1)
    await open(2); expectPlaces(2)
    await poll(); expectPlaces(2)
    await open(1); expectPlaces(1)
  })
})

describe('entries without an owner field (a backend from before slice 2)', () => {
  it('still show in the chat on screen, as before', async () => {
    await mount()
    await open(2)
    pending = { old: req('Mark-Old') }
    await poll()
    expect(inChat('Mark-Old')).toBe(1)
    expect(inTray('Mark-Old')).toBe(0)
    expect(api.chat.convStatus).toEqual({})
  })

  it('owner parsing: absent is legacy, an int is a chat, anything else is unknown', () => {
    expect(gateOwner({ tool: 'x' })).toBeUndefined()
    expect(gateOwner({ conversation_id: 7 })).toBe(7)
    expect(gateOwner({ conversation_id: null })).toBeNull()
    for (const bad of ['7', 0, -1, 1.5, {}, true]) expect(gateOwner({ conversation_id: bad })).toBeNull()
  })
})

describe('tray strings', () => {
  it('exist in both languages and differ', () => {
    for (const key of ['mcp.trayTitle', 'mcp.trayHint', 'mcp.trayTool', 'mcp.trayTarget',
      'mcp.trayTargetUnknown', 'mcp.trayProject', 'mcp.trayApprove', 'mcp.trayDeny',
      'mcp.trayApproved', 'mcp.trayDenied', 'mcp.trayCount']) {
      expect(translations.tr[key]).toBeTruthy()
      expect(translations.en[key]).toBeTruthy()
      expect(translations.tr[key]).not.toBe(translations.en[key])
    }
  })
})
