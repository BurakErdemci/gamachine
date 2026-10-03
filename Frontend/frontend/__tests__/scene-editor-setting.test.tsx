import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { readSceneEditor, writeSceneEditor, useSceneEditorSetting } from '../renderer/lib/sceneEditor'
import { UnityPage } from '../renderer/components/home/settings/SettingsPages'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { Workspace } from '../renderer/components/home/Workspace'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

beforeEach(() => localStorage.clear())
afterEach(cleanup)
const noop = () => {}
export const sidebarProps: React.ComponentProps<typeof Sidebar> = {
  isSidebarOpen: true, conversations: [{ id: 1, title: 'Chat', created_at: '', updated_at: '' }], activeConvId: 1,
  selectConversation: noop, createNewConversation: noop, deleteConversation: noop, editingId: null,
  setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop, workspacePath: null,
  closeWorkspace: noop, user: { id: 1, name: 'Burak', sessionToken: 'token' }, setShowSettings: noop, handleLogout: noop,
}

it('defaults off, persists, and tolerates denied storage', () => {
  expect(readSceneEditor()).toBe(false)
  writeSceneEditor(true)
  expect(localStorage.getItem('app-scene-editor')).toBe('on')
  expect(readSceneEditor()).toBe(true)
  writeSceneEditor(false)
  expect(localStorage.getItem('app-scene-editor')).toBe('off')
  const denied = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
  expect(readSceneEditor(denied)).toBe(false)
  expect(() => writeSceneEditor(true, denied)).not.toThrow()
})

it('the Unity settings switch updates all subscribers live', () => {
  const first = renderHook(useSceneEditorSetting)
  const second = renderHook(useSceneEditorSetting)
  render(<UnityPage status="connected" toggling={false} onToggle={noop} saved={vi.fn()} />)
  const toggle = screen.getByRole('switch', { name: ceviriUygula('tr', 'sceneEditor.setting') })
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(toggle)
  expect(first.result.current[0]).toBe(true)
  expect(second.result.current[0]).toBe(true)
  act(() => first.result.current[1](false))
  expect(toggle.getAttribute('aria-checked')).toBe('false')
})

it.each(['tr', 'en'] as const)('keeps the disabled sidebar and Scene label unchanged in %s', lang => {
  render(<LangContext.Provider value={{ lang, setLang: noop, t: (key, vars) => ceviriUygula(lang, key, vars) }}>
    <Sidebar {...sidebarProps} unityStatus="connected" />
    <Workspace open tab="sahne" width="dar" onTab={noop} onWidth={noop} onClose={noop}
      panes={{ sahne: null, dosyalar: null, kod: null, onizleme: null }} drawer={null} />
  </LangContext.Provider>)
  expect(document.querySelector('.side-tabs')).toBeNull()
  expect(screen.getByRole('tab', { name: lang === 'tr' ? 'Sahne' : 'Scene' })).toBeTruthy()
  act(() => writeSceneEditor(true))
  // Unity off still closes the feature gate.
  cleanup()
  render(<Sidebar {...sidebarProps} unityStatus="off" />)
  expect(screen.queryByRole('tablist')).toBeNull()
})
