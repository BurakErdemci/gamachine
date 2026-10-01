/**
 * Two sidebar findings from the P1 audit.
 *  - The project row closed the workspace in one click and silently dropped a dirty editor file.
 *  - A row whose status changed while its rename input was open jumped between the "Active
 *    tasks" and "Chats" lists, which remounted the input.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { Sidebar } from '../renderer/components/home/Sidebar'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const noop = () => {}
const USER = { id: 1, name: 'Burak', sessionToken: 't' } as any
const CONVS = [
  { id: 1, title: 'Alpha', parent_id: null },
  { id: 2, title: 'Beta', parent_id: null },
] as any

function props(over: Record<string, unknown> = {}) {
  return {
    ...({} as any),
    isSidebarOpen: true, sidebarTab: 'chats', setSidebarTab: noop,
    conversations: CONVS, activeConvId: 2, convStatus: {},
    selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
    editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
    fileTree: [], treeContextMenu: null, setTreeContextMenu: noop,
    user: USER, setShowSettings: noop, handleLogout: noop,
    workspacePath: 'C:/p/Arena', closeWorkspace: noop,
    ...over,
  }
}

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

describe('project switcher', () => {
  it('closes at once when nothing is unsaved', async () => {
    const close = vi.fn()
    const ask = vi.spyOn(window, 'confirm')
    render(<Sidebar {...props({ closeWorkspace: close, isDirty: false })} />)
    fireEvent.click(document.querySelector('button.project')!)
    await flush()
    expect(ask).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('asks first with a dirty file, and a cancel keeps the project open', async () => {
    const close = vi.fn()
    // No ConfirmDialogHost is mounted in this test, so confirmDialog falls back to window.confirm.
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<Sidebar {...props({ closeWorkspace: close, isDirty: true })} />)
    fireEvent.click(document.querySelector('button.project')!)
    await flush()
    expect(ask).toHaveBeenCalledTimes(1)
    expect(close).not.toHaveBeenCalled()
  })

  it('closes after the user confirms', async () => {
    const close = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<Sidebar {...props({ closeWorkspace: close, isDirty: true })} />)
    fireEvent.click(document.querySelector('button.project')!)
    await flush()
    expect(close).toHaveBeenCalledTimes(1)
  })
})

describe('rename input keeps its row in place', () => {
  it('a chat that starts running mid-rename stays in Chats with the same input', () => {
    const { rerender } = render(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp' })} />)
    const input = screen.getByDisplayValue('Alp')
    rerender(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp', convStatus: { 1: 'running' } })} />)
    // Same DOM node: not remounted into the other list.
    expect(screen.getByDisplayValue('Alp')).toBe(input)
    expect(screen.queryByText('Active tasks')).toBeNull()
  })

  it('a task that stops mid-rename stays in Active tasks with the same input', () => {
    const { rerender } = render(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp', convStatus: { 1: 'awaiting' } })} />)
    const input = screen.getByDisplayValue('Alp')
    rerender(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp', convStatus: {} })} />)
    expect(screen.getByDisplayValue('Alp')).toBe(input)
  })

  it('once the rename ends the row moves to where its status puts it', () => {
    const { rerender } = render(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp' })} />)
    rerender(<Sidebar {...props({ editingId: 1, tempTitle: 'Alp', convStatus: { 1: 'running' } })} />)
    rerender(<Sidebar {...props({ editingId: null, convStatus: { 1: 'running' } })} />)
    const taskRow = screen.getByTestId('conv-row-1')
    expect(taskRow.className).toContain('chat-quest')
  })
})
