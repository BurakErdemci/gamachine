import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, fireEvent, act, cleanup, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, on: vi.fn(() => () => {}) }
  return { invoke, confirm: vi.fn(), terms: [] as any[], fit: vi.fn(), editors: [] as any[],
    monaco: { editor: { defineTheme: vi.fn(), setTheme: vi.fn(), remeasureFonts: vi.fn(), setModelMarkers: vi.fn() },
      languages: { registerCompletionItemProvider: vi.fn(), registerHoverProvider: vi.fn(), registerDefinitionProvider: vi.fn() } } }
})
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: mocks.confirm }))
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  options: any; cols = 80; rows = 24
  constructor(options: any) { this.options = options; mocks.terms.push(this) }
  loadAddon() {} open() {} write() {} focus() {} clear() {} dispose() {}
  onData() { return { dispose() {} } }
} }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = mocks.fit } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('@monaco-editor/react', () => {
  const Surface = ({ beforeMount, onMount }: any) => {
    React.useEffect(() => {
      const editor = { updateOptions: vi.fn(), getModel: () => null,
        onDidChangeModel: vi.fn(), onDidFocusEditorWidget: vi.fn(), onDidBlurEditorWidget: vi.fn() }
      mocks.editors.push(editor)
      beforeMount?.(mocks.monaco)
      onMount?.(editor, mocks.monaco)
    }, [])
    return null
  }
  return { Editor: Surface, DiffEditor: Surface, loader: { config: vi.fn(), init: async () => ({}) } }
})

import axios from 'axios'
import { useFileSystem } from '../renderer/hooks/home/useFileSystem'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { FileCreationApproval } from '../renderer/components/home/FileCreationApproval'
import { DiffViewer } from '../renderer/components/home/DiffViewer'
import { KodPane, toggleTerminalDrawer } from '../renderer/components/home/Workspace'
import { usePendingChange } from '../renderer/lib/pendingChange'
import { EditorPanel } from '../renderer/components/home/EditorPanel'
import { TerminalPanel } from '../renderer/components/home/TerminalPanel'
import { EmptyChat } from '../renderer/components/home/EmptyChat'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { displayName } from '../renderer/lib/displayName'
import { translations } from '../renderer/lib/i18n'

const tr = translations.tr
const diff = { original_code: 'old', fixed_code: 'new', explanation: 'fix' }
const pendingFix = { data: diff, messageId: 1 }
const panelProps = () => ({
  messages: [{ id: 1, role: 'assistant', content: '', timestamp: '' }], activeConvId: 1,
  user: { name: 'local' }, loading: false, clearHistory: vi.fn(), lang: 'tr', thinkingLevel: 'auto',
  workspacePath: 'project', handleExportToUnity: vi.fn(), pendingGenFiles: null,
  setPendingGenFiles: vi.fn(), pendingFix, setPendingFix: vi.fn(), openedFilePath: 'Assets/A.cs',
  setCode: vi.fn(), setOriginalCode: vi.fn(), refreshFileTree: vi.fn(), analyzeProject: vi.fn(),
  openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef(), ipc: window.ipc,
  showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(), pendingDelete: null,
  setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(), onApproveCommand: vi.fn(),
  pendingQuestion: null, setPendingQuestion: vi.fn(), onAnswerQuestion: vi.fn(), deleteFile: vi.fn(),
  setIsTerminalOpen: vi.fn(), apiBase: 'http://backend', mcpGate: null,
  mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
})
function Strip() {
  const change = usePendingChange()
  return <KodPane workspacePath={null} openedFilePath={null} isDirty={false} onSave={vi.fn()}
    onCloseFile={vi.fn()} diff={change} change={change} fileEditor={null} diffEditor={null} />
}
beforeEach(() => {
  mocks.invoke.mockReset().mockResolvedValue({ success: true })
  mocks.confirm.mockReset()
  mocks.terms.length = 0; mocks.editors.length = 0; mocks.fit.mockClear()
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: {} })
})
afterEach(() => {
  cleanup()
  document.documentElement.style.removeProperty('--font-mono')
  vi.useRealTimers()
})

describe('F1 clean buffer lifecycle', () => {
  it('closing a clean file allows opening another without a discard prompt', async () => {
    const h = renderHook(() => useFileSystem('http://backend', null, vi.fn()))
    mocks.invoke.mockResolvedValueOnce({ path: 'Assets/A.cs', content: 'old' })
    await act(async () => { await h.result.current.openFile('Assets/A.cs') })
    act(() => h.result.current.closeFile())
    expect(h.result.current.isDirty).toBe(false)
    expect(h.result.current.code).toBe('')
    expect(h.result.current.openedFilePath).toBeNull()
    mocks.invoke.mockResolvedValueOnce({ path: 'Assets/B.cs', content: 'other' })
    await act(async () => { await h.result.current.openFile('Assets/B.cs') })
    expect(mocks.confirm).not.toHaveBeenCalled()
    expect(h.result.current.openedFilePath).toBe('Assets/B.cs')
  })
  it('a successful fix write makes the open buffer clean', async () => {
    const h = renderHook(() => useFileSystem('http://backend', null, vi.fn()))
    mocks.invoke.mockResolvedValueOnce({ path: 'Assets/A.cs', content: 'old' })
    await act(async () => { await h.result.current.openFile('Assets/A.cs') })
    const props: any = { ...panelProps(), setCode: h.result.current.setCode,
      setOriginalCode: h.result.current.setOriginalCode }
    render(<ChatPanel {...props} />)
    fireEvent.click(screen.getByRole('button', { name: tr['diff.accept'] }))
    await waitFor(() => expect(h.result.current.code).toBe('new'))
    await waitFor(() => expect(props.setPendingFix).toHaveBeenCalled())
    await waitFor(() => expect(h.result.current.isDirty).toBe(false))
  })
})

describe('F2 coherent pending entries', () => {
  it('home derives the strip diff directly from the same pending entry as its handlers', () => {
    const source = ts.createSourceFile('home.tsx', readFileSync('renderer/pages/home.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let initializer: ts.Expression | undefined
    const visit = (node: ts.Node) => {
      if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'kodDiff') initializer = node.initializer
      ts.forEachChild(node, visit)
    }
    visit(source)
    expect(initializer?.getText(source)).toBe('pendingChange')
    const props: Record<string, string> = {}
    const findPane = (node: ts.Node) => {
      if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(source) === 'KodPane') {
        for (const attr of node.attributes.properties) if (ts.isJsxAttribute(attr) && attr.initializer && ts.isJsxExpression(attr.initializer)) {
          props[attr.name.getText(source)] = attr.initializer.expression?.getText(source) ?? ''
        }
      }
      ts.forEachChild(node, findPane)
    }
    findPane(source)
    expect(props.diff).toBe('kodDiff')
    expect(props.change).toBe('pendingChange')
    expect(props.onCloseFile).toBe('fs.closeFile')
  })
  it('two card kinds keep the strip file, delta, and accept bound to one entry', async () => {
    const acceptCreate = vi.fn(async () => true), acceptFix = vi.fn()
    const files = [{ name: 'Created.cs', suggestedPath: 'Assets/Created.cs', code: 'a\nb' }]
    render(<><FileCreationApproval files={files} setDiffFile={vi.fn()} onAcceptOne={acceptCreate}
      onSkipOne={vi.fn()} onAcceptAll={vi.fn()} onDone={vi.fn()} />
      <DiffViewer diffData={diff} filename="Fixed.cs" onAccept={acceptFix} onReject={vi.fn()} /><Strip /></>)
    expect(screen.getByTestId('ws-pending').textContent).toContain('Fixed.cs')
    fireEvent.click(screen.getByRole('button', { name: tr['ws.accept'] }))
    expect(acceptFix).toHaveBeenCalledWith('new')
    expect(acceptCreate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Created.cs', { selector: '.approval-file-name' }))
    expect(screen.getByTestId('ws-pending').textContent).toContain('Fixed.cs')
  })
})

describe('F3 fix acceptance latch and captured path', () => {
  it('latches two calls before the busy state can render and releases after a rejection', async () => {
    let reject!: (reason: Error) => void
    const accept = vi.fn(() => new Promise<void>((_, fail) => { reject = fail }))
    render(<DiffViewer diffData={diff} filename="A.cs" onAccept={accept} onReject={vi.fn()} />)
    const entry = renderHook(() => usePendingChange())
    let first!: Promise<void>, second!: Promise<void>
    act(() => {
      first = entry.result.current!.accept() as unknown as Promise<void>
      second = entry.result.current!.accept() as unknown as Promise<void>
    })
    expect(accept).toHaveBeenCalledTimes(1)
    await act(async () => {
      reject(new Error('write failed'))
      await expect(first).rejects.toThrow('write failed')
      await second
    })
    expect(entry.result.current?.busy).toBe(false)
  })
  it('card and strip write once while busy, to the original path after the open file changes', async () => {
    let finish!: (v: any) => void
    mocks.invoke.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const props: any = panelProps()
    const view = render(<><ChatPanel {...props} /><Strip /></>)
    view.rerender(<><ChatPanel {...props} openedFilePath="Assets/B.cs" /><Strip /></>)
    const stripAccept = screen.getByRole('button', { name: tr['ws.accept'] }) as HTMLButtonElement
    fireEvent.click(stripAccept)
    fireEvent.click(screen.getByRole('button', { name: tr['diff.accept'] }))
    expect(stripAccept.disabled).toBe(true)
    expect(mocks.invoke).toHaveBeenCalledTimes(1)
    expect(mocks.invoke).toHaveBeenCalledWith('write-file', 'Assets/A.cs', 'new', 'project')
    await act(async () => finish({ success: true }))
    expect(props.setCode).not.toHaveBeenCalled()
    expect(props.setOriginalCode).not.toHaveBeenCalled()
  })
})

describe('F4 terminal menu visibility', () => {
  it('opens both surfaces when the workspace is hidden with the drawer still open', () => {
    const setWorkspace = vi.fn(), setDrawer = vi.fn()
    toggleTerminalDrawer(false, true, setWorkspace, setDrawer)
    expect(setWorkspace).toHaveBeenCalledWith(true)
    expect(setDrawer).toHaveBeenCalledWith(true)
    toggleTerminalDrawer(true, true, setWorkspace, setDrawer)
    expect(setDrawer).toHaveBeenLastCalledWith(false)
    toggleTerminalDrawer(true, false, setWorkspace, setDrawer)
    expect(setDrawer).toHaveBeenLastCalledWith(true)
  })
})

describe('F5 live code font', () => {
  it('updates mounted file and diff editors and the terminal without remounting', async () => {
    render(<><EditorPanel code="old" setCode={vi.fn()} openedFilePath="Assets/A.cs" isEditorFocused={false}
      setIsEditorFocused={vi.fn()} workspacePath="project" diffFile={null} />
      <EditorPanel code="" setCode={vi.fn()} openedFilePath={null} isEditorFocused={false}
        setIsEditorFocused={vi.fn()} workspacePath="project"
        diffFile={{ name: 'A.cs', suggestedPath: 'Assets/A.cs', code: 'new' }} />
      <TerminalPanel id="font-test" isOpen onClose={vi.fn()} workspacePath="project" /></>)
    await waitFor(() => expect(mocks.terms).toHaveLength(1))
    expect(mocks.editors).toHaveLength(2)
    const host = document.querySelector('.term-host')!;
    Object.defineProperties(host, { offsetWidth: { value: 800 }, offsetHeight: { value: 200 } })
    await act(async () => { document.documentElement.style.setProperty('--font-mono', 'Consolas, monospace') })
    for (const editor of mocks.editors) expect(editor.updateOptions).toHaveBeenCalledWith({ fontFamily: 'Consolas, monospace' })
    expect(mocks.monaco.editor.remeasureFonts).toHaveBeenCalled()
    expect(mocks.terms[0].options.fontFamily).toBe('Consolas, monospace')
    expect(mocks.fit).toHaveBeenCalled()
    expect(mocks.editors).toHaveLength(2)
    expect(mocks.terms).toHaveLength(1)
  })
})

describe('F6 Connections drawer', () => {
  it('shows backend online and Unity offline and preserves the historical request options', async () => {
    vi.mocked(axios.get).mockResolvedValueOnce({ data: {} }).mockRejectedValueOnce(new Error('offline'))
    render(<TerminalPanel id="connections-test" isOpen onClose={vi.fn()} workspacePath="project" apiUrl="http://backend:8000" />)
    fireEvent.click(screen.getByRole('tab', { name: tr['terminal.tabConnections'] }))
    await screen.findByText('Offline')
    expect(screen.getByText('Online').closest('.connection-row')?.textContent).toContain('Backend API')
    expect(screen.getByText('Offline').closest('.connection-row')?.textContent).toContain('Unity MCP')
    expect(axios.get).toHaveBeenCalledWith('http://backend:8000/health')
    expect(axios.get).toHaveBeenCalledWith('http://localhost:8080/health', { timeout: 1000 })
    vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: {} })
    fireEvent.click(screen.getByRole('button', { name: tr['report.refresh'] }))
    await waitFor(() => expect(screen.getByText('Online').closest('.connection-row')?.textContent).toContain('Unity MCP'))
  })
})

describe('F7/F8 shared display name', () => {
  it.each([null, undefined, '', '  ', 'local', ' LOCAL '])('treats %s as no name in the greeting and footer', name => {
    expect(displayName(name)).toBe('')
    const sidebar: any = { user: { name }, conversations: [], convStatus: {}, workspacePath: null,
      activeConvId: null, editingId: null, tempTitle: '', isSidebarOpen: true,
      setSettingsOpen: vi.fn() }
    render(<><EmptyChat userName={name} projectName="Project" onPick={vi.fn()} /><Sidebar {...sidebar} /></>)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tr['empty.titleNoName'].replace('{proje}', 'Project'))
    expect(document.querySelector('.foot-user')).toBeNull()
  })
  it('preserves real names on both surfaces', () => {
    expect(displayName(' Burak ')).toBe('Burak')
    render(<EmptyChat userName="Burak" projectName="Project" onPick={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('Burak')
  })
})
