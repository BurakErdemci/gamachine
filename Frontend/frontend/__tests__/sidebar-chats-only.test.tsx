import React from 'react'
import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'

afterEach(cleanup)

const noop = () => {}
const props: React.ComponentProps<typeof Sidebar> = {
  isSidebarOpen: true,
  conversations: [{ id: 1, title: 'Visible chat', created_at: '', updated_at: '' }], activeConvId: 1,
  selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
  editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
  workspacePath: null, closeWorkspace: noop,
  user: { id: 1, name: 'Burak', sessionToken: 't' }, setShowSettings: noop, handleLogout: noop,
}

it.each(['tr', 'en'] as const)('always shows chats without sidebar tabs in %s', (lang: Lang) => {
  const { container, rerender } = render(
    <LangContext.Provider value={{ lang, setLang: noop, t: (key, vars) => ceviriUygula(lang, key, vars) }}>
      <Sidebar {...props} />
    </LangContext.Provider>,
  )
  expect(screen.queryAllByRole('tab')).toHaveLength(0)
  expect(screen.queryByRole('tablist')).toBeNull()
  expect(screen.queryByText('Dosyalar')).toBeNull()
  expect(screen.queryByText('Files')).toBeNull()
  expect(container.querySelector('.chat-list')).toBeTruthy()
  expect(screen.getByTestId('conv-row-1').textContent).toContain('Visible chat')
  rerender(<Sidebar {...props} convStatus={{ 1: 'awaiting' }} />)
  expect(screen.getByTestId('conv-row-1').className).toContain('chat-quest')
  expect(screen.queryAllByRole('tab')).toHaveLength(0)
})
