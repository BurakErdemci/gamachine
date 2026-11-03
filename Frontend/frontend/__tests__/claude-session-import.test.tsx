/**
 * Claude Code session import UI: the dialog (preview, warnings, errors, import), the '⌨️'
 * divider in the thread, and the control panel's session row with its copy button.
 * HTTP is a mocked axios, as in the other component tests.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get, delete: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } }, post, get }
})

import axios from 'axios'
import { ImportSessionDialog, samePath } from '../renderer/components/home/ImportSessionDialog'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { ControlPanel } from '../renderer/components/home/ControlPanel'
import { EmptyChat } from '../renderer/components/home/EmptyChat'

const mocked = axios as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const SID = '11111111-2222-3333-4444-555555555555'

const preview = (over: object = {}) => ({
  data: {
    session_id: SID, cwd: 'C:\\Projects\\Game', cwd_exists: true, title: 'Fix the jump', model: 'claude-opus-5',
    total: 12, importable: true, modified_recently: false, modified_at: '2026-11-03 10:00:00', ...over,
  },
})
const apiError = (status: number, data: object) => Object.assign(new Error('x'), { response: { status, data } })

beforeEach(() => { mocked.get.mockReset(); mocked.post.mockReset() })
afterEach(() => cleanup())

const dialog = (props: object = {}) => {
  const onImported = vi.fn()
  const onClose = vi.fn()
  render(<ImportSessionDialog open onClose={onClose} onImported={onImported} API={API} token="tok"
    currentPath={'C:\\Projects\\Game'} {...props} />)
  return { onImported, onClose }
}
const typeId = () => fireEvent.change(screen.getByTestId('import-session-input'), { target: { value: `  ${SID}  ` } })
const press = () => fireEvent.click(screen.getByTestId('import-session-go'))

describe('ImportSessionDialog', () => {
  it('previews the session with the trimmed id, folder, title and message count', async () => {
    mocked.get.mockResolvedValue(preview())
    dialog()
    typeId(); press()
    await screen.findByTestId('import-session-preview')
    expect(mocked.get).toHaveBeenCalledWith(`${API}/claude-sessions/${SID}/preview`, { headers: { 'X-Session-Token': 'tok' } })
    expect(screen.getByText('Fix the jump')).toBeTruthy()
    expect(screen.getByTestId('import-session-preview').textContent).toContain('C:\\Projects\\Game')
    expect(screen.getByTestId('import-session-preview').textContent).toContain('12 mesaj')
    expect(screen.queryByTestId('import-session-recent')).toBeNull()
    expect(screen.queryByTestId('import-session-switch')).toBeNull()
    expect(mocked.post).not.toHaveBeenCalled()
  })

  it('warns when the session was used in the last minutes', async () => {
    mocked.get.mockResolvedValue(preview({ modified_recently: true }))
    dialog()
    typeId(); press()
    const warn = await screen.findByTestId('import-session-recent')
    expect(warn.textContent).toContain('Bu oturum son dakikalarda kullanılmış')
  })

  it('says the project will switch when the folder differs', async () => {
    mocked.get.mockResolvedValue(preview({ cwd: 'D:\\Other' }))
    dialog()
    typeId(); press()
    const note = await screen.findByTestId('import-session-switch')
    expect(note.textContent).toBe('Gamachine bu klasöre geçecek: D:\\Other')
  })

  it('treats separator style, case and a trailing slash as the same folder', () => {
    expect(samePath('C:\\Projects\\Game\\', 'c:/projects/game')).toBe(true)
    expect(samePath('C:\\Projects\\Game', 'C:\\Projects\\Other')).toBe(false)
    expect(samePath('C:\\Projects\\Game', null)).toBe(false)
  })

  it.each([
    [400, { error: 'bad_session_id' }, 'Bu bir oturum numarası değil.'],
    [404, { error: 'session_not_found' }, 'Bu numarada bir Claude Code oturumu bulunamadı.'],
    [500, { error: 'boom' }, 'Oturum içe aktarılamadı.'],
  ])('shows the preview error %i', async (status, body, text) => {
    mocked.get.mockRejectedValue(apiError(status, body))
    dialog()
    typeId(); press()
    expect((await screen.findByTestId('import-session-error')).textContent).toBe(text)
  })

  it('imports on the second press and hands the result on', async () => {
    mocked.get.mockResolvedValue(preview())
    const result = { chat_id: 41, workspace: 'C:\\Projects\\Game', title: 'Fix the jump', imported: 12, total: 12 }
    mocked.post.mockResolvedValue({ data: result })
    const { onImported } = dialog()
    typeId(); press()
    await screen.findByTestId('import-session-preview')
    press()
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(result))
    expect(mocked.post).toHaveBeenCalledWith(`${API}/claude-sessions/import`, { session_id: SID }, { headers: { 'X-Session-Token': 'tok' } })
    expect(mocked.get).toHaveBeenCalledTimes(1)
  })

  it('shows the missing-folder error from the import', async () => {
    mocked.get.mockResolvedValue(preview())
    mocked.post.mockRejectedValue(apiError(409, { error: 'cwd_missing', cwd: 'C:\\Gone' }))
    const { onImported } = dialog()
    typeId(); press()
    await screen.findByTestId('import-session-preview')
    press()
    expect((await screen.findByTestId('import-session-error')).textContent).toBe('Oturumun klasörü artık yok: C:\\Gone')
    expect(onImported).not.toHaveBeenCalled()
  })

  it('disables the buttons while a request is running', async () => {
    let release: (v: unknown) => void = () => {}
    mocked.get.mockReturnValue(new Promise((res) => { release = res }))
    dialog()
    typeId(); press()
    await waitFor(() => expect((screen.getByTestId('import-session-go') as HTMLButtonElement).disabled).toBe(true))
    expect((screen.getByText('Vazgeç') as HTMLButtonElement).disabled).toBe(true)
    release(preview())
    await screen.findByTestId('import-session-preview')
  })
})

describe('EmptyChat import link', () => {
  it('shows the link only when a handler is given', () => {
    const onImportSession = vi.fn()
    const { rerender } = render(<EmptyChat onPick={vi.fn()} onImportSession={onImportSession} />)
    fireEvent.click(screen.getByTestId('import-session-open'))
    expect(onImportSession).toHaveBeenCalledTimes(1)
    rerender(<EmptyChat onPick={vi.fn()} />)
    expect(screen.queryByTestId('import-session-open')).toBeNull()
  })
})

describe('ChatPanel import divider', () => {
  const panel = (messages: any[]) => render(<ChatPanel {...({
    messages, activeConvId: 7, user: { id: 1, name: 'b', sessionToken: 'tok' }, loading: false, lang: 'tr',
    thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
    pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(),
    openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn() }, showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(),
    pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
    onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
    onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
    mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
    activity: null,
  } as any)} />)

  it('renders the system row as a divider with its text, not a bubble', () => {
    panel([
      { id: 1, role: 'user', content: 'hello from the terminal' },
      { id: 2, role: 'system', content: '⌨️ Terminal oturumundan aktarıldı · 12 mesaj' },
    ])
    const divider = screen.getByTestId('import-divider')
    expect(divider.textContent).toContain('⌨️ Terminal oturumundan aktarıldı · 12 mesaj')
    expect(divider.closest('article')).toBeNull()
    expect(screen.queryByText('chat.wakeRow')).toBeNull()
    expect(document.querySelectorAll('[data-testid="import-divider"]').length).toBe(1)
  })

  it('leaves other system rows and mail notes alone', () => {
    panel([
      { id: 1, role: 'system', content: '📨 note from chat 3' },
      { id: 2, role: 'system', content: 'plain wake row' },
    ])
    expect(screen.queryByTestId('import-divider')).toBeNull()
    expect(screen.getByTestId('mail-note')).toBeTruthy()
  })
})

describe('ControlPanel Claude session row', () => {
  const COMMAND = `cd "C:\\Projects\\Game"; claude --resume ${SID}`
  const strip = (props: object = {}) => render(<ControlPanel {...({
    thinkingLevel: 'auto', setThinkingLevel: vi.fn(), activeConvId: 7, compactConversation: vi.fn(),
    isCompacting: false, isClaudeSubscription: true, apiBase: API, sessionToken: 'tok', showToast: vi.fn(),
    ...props,
  } as any)} />)

  it('shows the short id and copies exactly the backend command', async () => {
    mocked.get.mockResolvedValue({ data: { session_id: SID, workspace: 'C:\\Projects\\Game', command: COMMAND } })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const showToast = vi.fn()
    strip({ showToast })
    const button = await screen.findByTestId('claude-session-copy')
    expect(mocked.get).toHaveBeenCalledWith(`${API}/conversations/7/claude-session`, { headers: { 'X-Session-Token': 'tok' } })
    expect(button.textContent).toContain('Claude oturumu 11111111…')
    expect(button.textContent).toContain('Terminalde devam et — komutu kopyala')
    fireEvent.click(button)
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(COMMAND))
    expect(showToast).toHaveBeenCalledWith('Komut kopyalandı', 'success')
  })

  it('shows nothing for a null session id, on an error, and outside a Claude chat', async () => {
    mocked.get.mockResolvedValue({ data: { session_id: null } })
    const { unmount } = strip()
    await waitFor(() => expect(mocked.get).toHaveBeenCalled())
    expect(screen.queryByTestId('claude-session-copy')).toBeNull()
    unmount()

    mocked.get.mockReset()
    mocked.get.mockRejectedValue(new Error('down'))
    const second = strip()
    await waitFor(() => expect(mocked.get).toHaveBeenCalled())
    expect(screen.queryByTestId('claude-session-copy')).toBeNull()
    second.unmount()

    mocked.get.mockReset()
    strip({ isClaudeSubscription: false })
    expect(mocked.get).not.toHaveBeenCalled()
  })

  it('shows a visible strip button that copies exactly the command', async () => {
    mocked.get.mockResolvedValue({ data: { session_id: SID, workspace: 'C:\Projects\Game', command: COMMAND } })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const showToast = vi.fn()
    strip({ showToast })
    const button = await screen.findByTestId('claude-session-strip')
    expect(button.textContent).toContain('Terminalde devam et')
    expect(button.getAttribute('title')).toBe(`Claude oturumu ${SID} — komutu kopyala`)
    expect(mocked.get).toHaveBeenCalledTimes(1)
    fireEvent.click(button)
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(COMMAND))
    expect(showToast).toHaveBeenCalledWith('Komut kopyalandı', 'success')
    expect(screen.getByTestId('claude-session-copy')).toBeTruthy()
  })

  it('has no strip button for a null session id or a non-Claude chat', async () => {
    mocked.get.mockResolvedValue({ data: { session_id: null } })
    const { unmount } = strip()
    await waitFor(() => expect(mocked.get).toHaveBeenCalled())
    expect(screen.queryByTestId('claude-session-strip')).toBeNull()
    unmount()
    mocked.get.mockReset()
    strip({ isClaudeSubscription: false })
    expect(mocked.get).not.toHaveBeenCalled()
    expect(screen.queryByTestId('claude-session-strip')).toBeNull()
  })

  it('reads the session again when a turn ends', async () => {
    mocked.get.mockResolvedValue({ data: { session_id: null } })
    const { rerender } = strip({ loading: true })
    expect(mocked.get).not.toHaveBeenCalled()
    mocked.get.mockResolvedValue({ data: { session_id: SID, workspace: 'w', command: COMMAND } })
    rerender(<ControlPanel {...({
      thinkingLevel: 'auto', setThinkingLevel: vi.fn(), activeConvId: 7, compactConversation: vi.fn(),
      isCompacting: false, isClaudeSubscription: true, apiBase: API, sessionToken: 'tok', loading: false,
    } as any)} />)
    expect(await screen.findByTestId('claude-session-copy')).toBeTruthy()
  })
})
