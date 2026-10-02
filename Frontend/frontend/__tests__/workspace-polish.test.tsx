import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, fireEvent, act, cleanup, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, on: vi.fn(() => () => {}) }
  return { invoke, editors: [] as any[], monaco: {
    editor: { defineTheme: vi.fn(), setTheme: vi.fn(), remeasureFonts: vi.fn(), setModelMarkers: vi.fn() },
    languages: { registerCompletionItemProvider: vi.fn(), registerHoverProvider: vi.fn(), registerDefinitionProvider: vi.fn() },
  } }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  options: any; cols = 80; rows = 24
  constructor(options: any) { this.options = options }
  loadAddon() {} open() {} write() {} focus() {} clear() {} dispose() {}
  onData() { return { dispose() {} } }
} }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('@monaco-editor/react', () => {
  const Surface = ({ beforeMount, onMount }: any) => {
    React.useEffect(() => {
      const listeners = new Set<() => void>()
      const editor = { updateOptions: vi.fn(), getModel: () => null,
        onDidDispose: (fn: () => void) => { listeners.add(fn); return { dispose: () => listeners.delete(fn) } },
        onDidChangeModel: vi.fn(), onDidFocusEditorWidget: vi.fn(), onDidBlurEditorWidget: vi.fn() }
      mocks.editors.push(editor)
      beforeMount?.(mocks.monaco)
      onMount?.(editor, mocks.monaco)
      return () => { listeners.forEach(fn => fn()); editor.updateOptions.mockClear() }
    }, [])
    return null
  }
  return { Editor: Surface, DiffEditor: ({ ...props }: any) => <Surface {...props} />,
    loader: { config: vi.fn(), init: async () => ({}) } }
})
vi.mock('framer-motion', () => ({ AnimatePresence: ({ children }: any) => <>{children}</>,
  motion: { div: ({ initial, animate, exit, transition, ...props }: any) => <div {...props} />,
    img: ({ initial, animate, transition, ...props }: any) => <img {...props} /> } }))

import axios from 'axios'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { DiffViewer } from '../renderer/components/home/DiffViewer'
import { EditorPanel } from '../renderer/components/home/EditorPanel'
import { FileCreationApproval } from '../renderer/components/home/FileCreationApproval'
import { TerminalPanel } from '../renderer/components/home/TerminalPanel'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { defineUnityTheme, watchEditorFont } from '../renderer/components/home/monaco-theme'
import { publishPendingChange, usePendingChange } from '../renderer/lib/pendingChange'
import { displayName } from '../renderer/lib/displayName'
import { translations } from '../renderer/lib/i18n'

const tr = translations.tr
const diff = { original_code: 'old', fixed_code: 'new', explanation: 'fix' }
const panelProps = (): any => ({
  messages: [{ id: 1, role: 'assistant', content: '', timestamp: '' }], activeConvId: 1,
  user: { name: 'local' }, loading: false, clearHistory: vi.fn(), lang: 'tr', thinkingLevel: 'auto',
  workspacePath: 'project', handleExportToUnity: vi.fn(), pendingGenFiles: null,
  setPendingGenFiles: vi.fn(), pendingFix: { data: diff, messageId: 1 }, setPendingFix: vi.fn(), openedFilePath: 'Assets/A.cs',
  setCode: vi.fn(), setOriginalCode: vi.fn(), refreshFileTree: vi.fn(), analyzeProject: vi.fn(),
  openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef(), ipc: window.ipc,
  showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(), pendingDelete: null,
  setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(), onApproveCommand: vi.fn(),
  pendingQuestion: null, setPendingQuestion: vi.fn(), onAnswerQuestion: vi.fn(), deleteFile: vi.fn(),
  setIsTerminalOpen: vi.fn(), apiBase: 'http://backend', mcpGate: null,
  mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
})
const pickerProps = () => ({ userName: 'local', lastWorkspacePath: 'project/recent',
  onOpenWorkspaceDialog: vi.fn(), onSelectLastWorkspace: vi.fn(), onLogout: vi.fn() })
beforeEach(() => {
  mocks.invoke.mockReset().mockResolvedValue({ success: true })
  mocks.editors.length = 0
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: {} })
})
afterEach(() => {
  cleanup()
  document.documentElement.style.removeProperty('--font-mono')
  vi.useRealTimers()
})
const changeFont = async () => {
  await act(async () => { document.documentElement.style.setProperty('--font-mono', 'Consolas, monospace') })
}

describe('workspace polish regressions', () => {
  it.each([false, true])('G1 saves the baseline and preserves the pending buffer (edited=%s)', async edited => {
    let finish!: (value: any) => void
    mocks.invoke.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const props = panelProps()
    function Harness() {
      const [code, setCode] = React.useState('old')
      const [baseline, setOriginalCode] = React.useState('old')
      return <><output data-testid="buffer">{code}</output><output data-testid="baseline">{baseline}</output>
        <output data-testid="dirty">{String(code !== baseline)}</output>
        <button onClick={() => setCode('user edits')}>type</button>
        <ChatPanel {...props} setCode={setCode} setOriginalCode={setOriginalCode} /></>
    }
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: tr['diff.accept'] }))
    expect(screen.getByTestId('buffer').textContent).toBe('new')
    if (edited) fireEvent.click(screen.getByText('type'))
    await act(async () => finish({ success: true }))
    expect(screen.getByTestId('buffer').textContent).toBe(edited ? 'user edits' : 'new')
    expect(screen.getByTestId('baseline').textContent).toBe('new')
    expect(screen.getByTestId('dirty').textContent).toBe(String(edited))
    expect(mocks.invoke).toHaveBeenCalledWith('write-file', 'Assets/A.cs', 'new', 'project')
  })

  it('G2 unregisters the inner diff editor when the card becomes applied', async () => {
    const props = { diffData: diff, onAccept: vi.fn(), onReject: vi.fn() }
    const view = render(<DiffViewer {...props} />)
    const editor = mocks.editors[0]
    view.rerender(<DiffViewer {...props} applied />)
    await changeFont()
    expect(editor.updateOptions).not.toHaveBeenCalled()
  })
  it('G2 unregisters editors on both directions of the file/diff swap', async () => {
    const props = { code: 'old', setCode: vi.fn(), openedFilePath: 'Assets/A.cs', isEditorFocused: false,
      setIsEditorFocused: vi.fn(), workspacePath: 'project' }
    const view = render(<EditorPanel {...props} diffFile={null} />)
    const file = mocks.editors[0]
    view.rerender(<EditorPanel {...props} diffFile={{ name: 'A.cs', suggestedPath: 'Assets/A.cs', code: 'new' }} />)
    const diffEditor = mocks.editors[1]
    await changeFont()
    expect(file.updateOptions).not.toHaveBeenCalled()
    expect(diffEditor.updateOptions).toHaveBeenCalledWith({ fontFamily: 'Consolas, monospace' })
    view.rerender(<EditorPanel {...props} diffFile={null} />)
    await act(async () => { document.documentElement.style.setProperty('--font-mono', 'monospace') })
    expect(diffEditor.updateOptions).not.toHaveBeenCalled()
    expect(mocks.editors[2].updateOptions).toHaveBeenCalledWith({ fontFamily: 'monospace' })
  })
  it('G2 a throwing editor cannot block later editors or font measurement', async () => {
    defineUnityTheme(mocks.monaco as any)
    const broken = { updateOptions: vi.fn() }, healthy = { updateOptions: vi.fn() }
    const stopBroken = watchEditorFont(broken), stopHealthy = watchEditorFont(healthy)
    broken.updateOptions.mockImplementation(() => { throw new Error('disposed') })
    mocks.monaco.editor.remeasureFonts.mockClear()
    try {
      await changeFont()
      expect(healthy.updateOptions).toHaveBeenCalledWith({ fontFamily: 'Consolas, monospace' })
      expect(mocks.monaco.editor.remeasureFonts).toHaveBeenCalled()
    } finally { stopBroken(); stopHealthy() }
  })
  it.each([false, true])('G3 starts no Unity request after unmount (backend rejects=%s)', async rejects => {
    let finish!: () => void
    vi.mocked(axios.get).mockImplementationOnce(() => new Promise((resolve, reject) => {
      finish = () => rejects ? reject(new Error('offline')) : resolve({ data: {} })
    }))
    const view = render(<TerminalPanel id="polish" isOpen onClose={vi.fn()} workspacePath="project" apiUrl="http://backend:8000" />)
    fireEvent.click(screen.getByRole('tab', { name: tr['terminal.tabConnections'] }))
    expect(axios.get).toHaveBeenCalledTimes(1)
    view.unmount()
    await act(async () => finish())
    expect(axios.get).toHaveBeenCalledTimes(1)
  })
  it('G4 replaces an existing id in place, notifies, and retains the updated entry', () => {
    const entry = renderHook(() => usePendingChange())
    const first = { id: 'first', name: 'first', original: '', modified: '', accept: vi.fn(), reject: vi.fn() }
    const second = { ...first, id: 'second', name: 'second' }
    let stopFirst!: () => void, stopSecond!: () => void, stopUpdated!: () => void
    act(() => { stopFirst = publishPendingChange(first); stopSecond = publishPendingChange(second) })
    act(() => { stopUpdated = publishPendingChange({ ...first, busy: true }) })
    expect(entry.result.current?.id).toBe('second')
    act(() => { stopFirst(); stopSecond() })
    expect(entry.result.current?.id).toBe('first')
    expect(entry.result.current?.busy).toBe(true)
    act(() => stopUpdated())
    expect(entry.result.current).toBeNull()
  })
  it.each(['fix', 'create'])('G4 a busy %s card does not displace a newer card', async kind => {
    let finish!: (value: any) => void
    const accept = vi.fn(() => new Promise<any>(resolve => { finish = resolve }))
    const entry = renderHook(() => usePendingChange())
    const files = [{ name: 'Created.cs', suggestedPath: 'Assets/Created.cs', code: 'new' }]
    render(<>{kind === 'fix' ? <DiffViewer diffData={diff} filename="Older.cs" onAccept={accept} onReject={vi.fn()} /> :
      <FileCreationApproval files={files} setDiffFile={vi.fn()} onAcceptOne={accept} onSkipOne={vi.fn()}
        onAcceptAll={vi.fn()} onDone={vi.fn()} />}
      <DiffViewer diffData={diff} filename="Newer.cs" onAccept={vi.fn()} onReject={vi.fn()} /></>)
    expect(entry.result.current?.name).toBe('Newer.cs')
    fireEvent.click(screen.getAllByRole('button', { name: kind === 'fix' ? tr['diff.accept'] : tr['approval.apply'] })[0])
    expect(accept).toHaveBeenCalledTimes(1)
    expect(entry.result.current?.name).toBe('Newer.cs')
    await act(async () => finish(true))
    expect(entry.result.current?.name).toBe('Newer.cs')
  })
  it('G5 hides the placeholder in the welcome and uses Gamachine as the home title', () => {
    render(<WorkspaceScreen {...pickerProps()} />)
    expect(screen.getByRole('heading').textContent?.trim()).toBe(tr['workspace.welcomeNoName'])
    const source = ts.createSourceFile('home.tsx', readFileSync('renderer/pages/home.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let expression = ''
    const visit = (node: ts.Node) => {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(source) === 'title') {
        const child = node.children[0]
        if (ts.isJsxExpression(child)) expression = child.expression!.getText(source)
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    expect(expression).not.toBe('')
    const js = ts.transpile(`(${expression})`, { target: ts.ScriptTarget.ES2020 })
    // The name now comes from useDisplayName (`me`), not auth.user (display name, 2 Oct 2026).
    const title = new Function('me', 'displayName', `return ${js}`)
    expect(title({ name: 'local' }, displayName)).toBe('Gamachine')
    expect(title({ name: '' }, displayName)).toBe('Gamachine')
    expect(title({ name: ' Burak ' }, displayName)).toBe('Gamachine | Burak')
  })
  it.each(['path', 'workspace', 'ipc'])('G6 missing %s warns without writes or optimistic edits and stays pending', async missing => {
    const props = panelProps()
    if (missing === 'path') props.openedFilePath = null
    if (missing === 'workspace') props.workspacePath = null
    if (missing === 'ipc') props.ipc = null
    render(<ChatPanel {...props} />)
    fireEvent.click(screen.getByRole('button', { name: tr['diff.accept'] }))
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith(tr['chat.fixNoTarget'], 'warning'))
    expect(mocks.invoke).not.toHaveBeenCalled()
    expect(props.setCode).not.toHaveBeenCalled()
    expect(props.setPendingFix).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: tr['diff.accept'] })).toBeTruthy()
    expect(translations.en['chat.fixNoTarget']).toBe('This fix has no target file; open the file first.')
  })
  it('G7 uses shell tokens without raw colours and preserves picker handlers', () => {
    const props = pickerProps()
    const view = render(<WorkspaceScreen {...props} />)
    const classes = [...view.container.querySelectorAll('[class]')].map(el => el.className).join(' ')
    expect(classes).not.toMatch(/(?:bg|text|border|from|to|shadow)-(?:slate|blue|violet|red|white|black)|\[#[0-9a-f]+\]/i)
    expect(classes).toContain('var(--shell-bg)')
    expect(classes).toContain('btn-primary')
    fireEvent.click(screen.getByRole('button', { name: tr['workspace.selectFolder'] }))
    fireEvent.click(screen.getByRole('button', { name: /recent/ }))
    fireEvent.click(screen.getByRole('button', { name: tr['workspace.logout'] }))
    expect(props.onOpenWorkspaceDialog).toHaveBeenCalledTimes(1)
    expect(props.onSelectLastWorkspace).toHaveBeenCalledTimes(1)
    expect(props.onLogout).toHaveBeenCalledTimes(1)
  })
})
