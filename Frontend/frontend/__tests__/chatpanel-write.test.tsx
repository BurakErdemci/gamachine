/**
 * ChatPanel write-file paths (diff accept, accept one, accept all): a rejected
 * IPC call (remote mode rejects instead of returning {success:false}) shows the
 * error toast and never escapes as an unhandled rejection; a success applies.
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

describe('ChatPanel write-file', () => {
  const fixProps = () => ({
    pendingFix: { data: { fixed_code: 'fixed' }, messageId: 1 },
    openedFilePath: '/ws/Assets/A.cs',
  })

  it('diff accept: a rejected write shows the error toast and does not throw', async () => {
    invoke.mockRejectedValue(new Error('remote down'))
    panel(fixProps())
    fireEvent.click(screen.getByText('diff-accept'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(failText('A.cs', 'remote down'), 'error'))
    await new Promise(r => setTimeout(r, 0))
    expect(unhandled).toEqual([])
  })

  it('diff accept: a successful write still applies', async () => {
    invoke.mockResolvedValue({ success: true })
    const setPendingFix = vi.fn()
    panel({ ...fixProps(), setPendingFix })
    fireEvent.click(screen.getByText('diff-accept'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(cevir('chat.fileUpdated'), 'success'))
    expect(setPendingFix).toHaveBeenCalled()
    expect(refreshFileTree).toHaveBeenCalled()
  })

  const genProps = (files: any[]) => ({ pendingGenFiles: { files, messageId: 1 } })

  it('accept one: a rejected write shows the error toast and does not throw', async () => {
    invoke.mockRejectedValue(new Error('remote down'))
    panel(genProps([FILE]))
    fireEvent.click(screen.getByText('accept-one'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(failText('A.cs', 'remote down'), 'error'))
    await new Promise(r => setTimeout(r, 0))
    expect(unhandled).toEqual([])
    expect(refreshFileTree).not.toHaveBeenCalled()
  })

  it('accept one: a successful write still applies', async () => {
    invoke.mockResolvedValue({ success: true })
    const setCode = vi.fn()
    panel({ ...genProps([FILE]), setCode, openedFilePath: FILE.suggestedPath })
    fireEvent.click(screen.getByText('accept-one'))
    await waitFor(() => expect(refreshFileTree).toHaveBeenCalled())
    expect(setCode).toHaveBeenCalledWith(FILE.code)
    expect(showToast).not.toHaveBeenCalled()
  })

  it('accept all: a rejected write toasts per file, continues, and does not throw', async () => {
    invoke.mockRejectedValueOnce(new Error('remote down')).mockResolvedValueOnce({ success: true })
    panel(genProps([FILE, FILE2]))
    fireEvent.click(screen.getByText('accept-all'))
    await waitFor(() => expect(refreshFileTree).toHaveBeenCalled())
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith(failText('A.cs', 'remote down'), 'error')
    expect(invoke).toHaveBeenCalledTimes(2)
    await new Promise(r => setTimeout(r, 0))
    expect(unhandled).toEqual([])
  })

  it('accept all: successful writes still apply', async () => {
    invoke.mockResolvedValue({ success: true })
    panel(genProps([FILE, FILE2]))
    fireEvent.click(screen.getByText('accept-all'))
    await waitFor(() => expect(refreshFileTree).toHaveBeenCalled())
    expect(invoke).toHaveBeenCalledTimes(2)
    expect(showToast).not.toHaveBeenCalled()
  })
})
