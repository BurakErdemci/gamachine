/**
 * Small user-facing fixes: a failed pending-fix write leaves the editor content alone.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

const invoke = vi.fn()

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

vi.mock('../renderer/components/home/DiffViewer', () => ({
  DiffViewer: ({ onAccept }: any) => (
    <button onClick={() => { void onAccept('fixed') }}>diff-accept</button>
  ),
}))

vi.mock('../renderer/components/home/FileCreationApproval', () => ({
  FileCreationApproval: ({ files, onAcceptOne, onAcceptAll }: any) => (
    <div>
      <button onClick={() => { void onAcceptOne(files[0]) }}>accept-one</button>
      <button onClick={() => { void onAcceptAll(files) }}>accept-all</button>
    </div>
  ),
}))

import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { cevir } from '../renderer/lib/i18n'

const FILE = { name: 'A.cs', suggestedPath: '/ws/Assets/A.cs', code: 'class A {}' } as any
const FILE2 = { name: 'B.cs', suggestedPath: '/ws/Assets/B.cs', code: 'class B {}' } as any
const MSGS = [{ id: 1, role: 'assistant', content: 'hi', smells: [], timestamp: 't' }]

let showToast: ReturnType<typeof vi.fn>
let refreshFileTree: ReturnType<typeof vi.fn>
let unhandled: unknown[]
const onUnhandled = (e: unknown) => { unhandled.push(e) }

const panel = (over: Record<string, unknown> = {}) => render(<ChatPanel {...({
  messages: MSGS, activeConvId: 7, user: { id: 1, name: 'b', sessionToken: 't' }, loading: false, lang: 'tr',
  thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
  pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
  openedFilePath: null, setCode: vi.fn(), refreshFileTree,
  openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
  ipc: { invoke }, showToast, diffFile: null, setDiffFile: vi.fn(),
  pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
  onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
  onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: 'http://127.0.0.1:8000',
  mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
  activity: null,
  ...over,
} as any)} />)

const failText = (name: string, hata: string) => cevir('chat.writeFailed', { ad: name, hata })

beforeEach(() => {
  invoke.mockReset()
  showToast = vi.fn()
  refreshFileTree = vi.fn()
  unhandled = []
  process.on('unhandledRejection', onUnhandled)
})
afterEach(() => {
  process.off('unhandledRejection', onUnhandled)
  cleanup()
  vi.restoreAllMocks()
})

describe('pending fix accept via DiffViewer', () => {
  const fixProps = () => ({
    pendingFix: { data: { fixed_code: 'fixed', original_code: 'orig' }, messageId: 1 },
    openedFilePath: '/ws/Assets/A.cs',
  })

  it('a failed write puts the original content back and shows the error toast', async () => {
    invoke.mockResolvedValue({ success: false, error: 'disk full' })
    const setCode = vi.fn()
    panel({ ...fixProps(), setCode })
    fireEvent.click(screen.getByText('diff-accept'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.any(String), 'error'))
    expect(setCode).toHaveBeenLastCalledWith('orig')
  })

  it('a rejected write puts the original content back', async () => {
    invoke.mockRejectedValue(new Error('remote down'))
    const setCode = vi.fn()
    panel({ ...fixProps(), setCode })
    fireEvent.click(screen.getByText('diff-accept'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(failText('A.cs', 'remote down'), 'error'))
    expect(setCode).toHaveBeenLastCalledWith('orig')
  })

  it('a successful write updates the editor', async () => {
    invoke.mockResolvedValue({ success: true })
    const setCode = vi.fn()
    panel({ ...fixProps(), setCode })
    fireEvent.click(screen.getByText('diff-accept'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(cevir('chat.fileUpdated'), 'success'))
    expect(setCode).toHaveBeenCalledWith('fixed')
  })
})
