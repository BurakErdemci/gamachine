/**
 * The workspace panel (P3): tabs, widths, open routing, and the Kod tab's "waiting for your
 * approval" strip bound to the card's own handlers.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, renderHook, act, waitFor } from '@testing-library/react'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

import { useWorkspacePanel, tabForPath } from '../renderer/lib/workspacePanel'
import { publishPendingChange, usePendingChange, lineDelta } from '../renderer/lib/pendingChange'
import { Workspace, KodPane } from '../renderer/components/home/Workspace'
import { FileCreationApproval } from '../renderer/components/home/FileCreationApproval'
import { translations } from '../renderer/lib/i18n'

afterEach(cleanup)
const tr = translations.tr

describe('useWorkspacePanel.reveal', () => {
  it('opens a closed panel even when the same file is asked for again (P2 audit)', () => {
    const { result } = renderHook(() => useWorkspacePanel())
    act(() => result.current.reveal('kod'))
    act(() => result.current.setOpen(false))
    expect(result.current.open).toBe(false)
    // nothing about the file changed; the request alone must bring the panel back
    act(() => result.current.reveal('kod'))
    expect(result.current.open).toBe(true)
    expect(result.current.tab).toBe('kod')
  })

  it('widens narrow to half for Kod and Önizleme, leaves a width the user chose', () => {
    const { result } = renderHook(() => useWorkspacePanel())
    expect(result.current.width).toBe('dar')
    act(() => result.current.reveal('onizleme'))
    expect(result.current.width).toBe('yarim')
    act(() => result.current.setWidth('odak'))
    act(() => result.current.reveal('kod'))
    expect(result.current.width).toBe('odak')
    act(() => result.current.setWidth('dar'))
    act(() => result.current.reveal('dosyalar'))
    expect(result.current.width).toBe('dar')
    act(() => result.current.reveal('kod', { widen: false }))
    expect(result.current.width).toBe('dar')
  })

  it('routes text to Kod and models / images to Önizleme', () => {
    expect(tabForPath('Assets/Scripts/Player.cs')).toBe('kod')
    expect(tabForPath('Assets/Models/hero.fbx')).toBe('onizleme')
    expect(tabForPath('Assets/Sprites/coin.png')).toBe('onizleme')
  })
})

describe('Workspace frame', () => {
  const panes = { sahne: <p>scene</p>, dosyalar: <p>files</p>, kod: <p>code</p>, onizleme: <p>preview</p> }

  it('marks the selected tab and width, keeps every pane mounted', () => {
    const onTab = vi.fn(); const onWidth = vi.fn()
    render(<Workspace open tab="kod" onTab={onTab} width="yarim" onWidth={onWidth} onClose={vi.fn()} panes={panes} drawer={null} />)
    const ws = screen.getByTestId('workspace')
    expect(ws.getAttribute('data-tab')).toBe('kod')
    // the tab's name also carries its "change waiting" mark when one shows, hence the prefix match
    expect(screen.getByRole('tab', { name: new RegExp(`^${tr['ws.tabCode']}`) }).getAttribute('aria-selected')).toBe('true')
    // hidden panes stay in the DOM: Monaco and the 3D stage must not remount on a tab switch
    expect(screen.getByText('scene')).toBeTruthy()
    expect(screen.getByText('preview')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: tr['ws.tabPreview'] }))
    expect(onTab).toHaveBeenCalledWith('onizleme')
    expect(screen.getByRole('button', { name: tr['ws.widthHalf'] }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: tr['ws.widthFocus'] }))
    expect(onWidth).toHaveBeenCalledWith('odak')
  })
})

describe('pending change channel', () => {
  it('shows the newest change and withdraws it', () => {
    const { result } = renderHook(() => usePendingChange())
    let stop: () => void = () => {}
    act(() => { stop = publishPendingChange({ id: 'a', name: 'A.cs', original: '', modified: 'x', accept: vi.fn(), reject: vi.fn() }) })
    expect(result.current?.name).toBe('A.cs')
    act(() => stop())
    expect(result.current).toBeNull()
  })

  it('counts added / removed lines for the crumb', () => {
    expect(lineDelta('', 'a\nb\nc')).toEqual({ add: 3, del: 0 })
    expect(lineDelta('a\nb\nc', 'a\nB\nc\nd')).toEqual({ add: 2, del: 1 })
  })
})

// Stable, as pendingGenFiles.files is in the app (the card's effects key on the array).
const FILES = [
  { name: 'A.cs', code: 'class A {}', suggestedPath: 'Assets/A.cs' },
  { name: 'B.cs', code: 'class B {}', suggestedPath: 'Assets/B.cs', originalCode: 'class B { old }' },
]

/** The card and the Kod strip on one screen, as home.tsx wires them. */
function Both({ onAcceptOne, onSkipOne }: { onAcceptOne: any; onSkipOne: any }) {
  const change = usePendingChange()
  const [diff, setDiff] = React.useState<any>(null)
  return (
    <>
      <FileCreationApproval
        files={FILES}
        onAcceptOne={onAcceptOne}
        onSkipOne={onSkipOne}
        onAcceptAll={vi.fn(async () => true)}
        onDone={vi.fn()}
        setDiffFile={setDiff}
      />
      <KodPane
        workspacePath={null} openedFilePath={null} isDirty={false} onSave={vi.fn()} onCloseFile={vi.fn()}
        diff={diff ? { name: diff.name, path: diff.suggestedPath, original: diff.originalCode ?? '', modified: diff.code } : null}
        change={change} fileEditor={null} diffEditor={null}
      />
    </>
  )
}

describe('Kod strip and the card are one decision', () => {
  it('Accept in the strip runs the card\'s own apply for the file on screen; the card moves on', async () => {
    const onAcceptOne = vi.fn(async (_file: any) => true)
    render(<Both onAcceptOne={onAcceptOne} onSkipOne={vi.fn()} />)
    const strip = await screen.findByTestId('ws-pending')
    expect(strip.textContent).toContain(tr['ws.pendingK'])
    fireEvent.click(screen.getByRole('button', { name: tr['ws.accept'] }))
    await waitFor(() => expect(onAcceptOne).toHaveBeenCalledTimes(1))
    expect(onAcceptOne.mock.calls[0][0].name).toBe('A.cs')
    // the card advanced to B.cs; the strip follows it
    await waitFor(() => expect(screen.getByTestId('ws-pending').textContent).toContain('B.cs'))
    const fileB = screen.getAllByText('B.cs').map(n => n.closest('li')).find(Boolean)!
    expect(fileB.getAttribute('data-active')).toBe('true')
  })

  it('Reject in the strip is the card\'s skip; deciding in the card clears the strip', async () => {
    const onSkipOne = vi.fn((_file: any) => {})
    const onAcceptOne = vi.fn(async (_file: any) => true)
    render(<Both onAcceptOne={onAcceptOne} onSkipOne={onSkipOne} />)
    await screen.findByTestId('ws-pending')
    fireEvent.click(screen.getByRole('button', { name: tr['ws.reject'] }))
    expect(onSkipOne).toHaveBeenCalledTimes(1)
    expect(onSkipOne.mock.calls[0][0].name).toBe('A.cs')
    // now decide the last file in the card itself
    fireEvent.click(screen.getByRole('button', { name: tr['approval.apply'] }))
    await waitFor(() => expect(onAcceptOne).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.queryByTestId('ws-pending')).toBeNull())
  })
})
