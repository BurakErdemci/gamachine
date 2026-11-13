/** The create button stays off until a folder is chosen, and presses still name what is missing. */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '@/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula, translations } from '@/lib/i18n'
import { resetHubStore } from '@/lib/hub'
import { AppearanceContext, DEFAULTS, THEMES, type Theme } from '@/lib/appearance'

const en = translations.en
const ipc = async (channel: string, arg: any) => {
  if (channel === 'hub-list-editors') return { editors: [{ version: '6000.2.4f1', path: '/u/Unity' }] }
  if (channel === 'hub-list-templates') return { templates: [{ id: 'com.unity.template.3d-cross-platform-17.0.6', label: '3d', file: 'b.tgz' }] }
  if (channel === 'hub-pick-parent-dir') return { path: '/Users/me/Games' }
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok', unityVersion: '6000.2.4f1' }))
  return null
}

beforeEach(() => {
  resetHubStore()
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [] } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const open = (theme: Theme = DEFAULTS.theme) => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  render(<AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, theme }, setAppearance: () => {} }}><LangContext.Provider value={value}><WorkspaceScreen api="http://backend" user={{ id: 7, name: 'B', sessionToken: 't' }} userName="B"
    onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn(async () => true)} onLogout={vi.fn()} showToast={vi.fn()} /></LangContext.Provider></AppearanceContext.Provider>)
  fireEvent.click(screen.getByTestId('hub-tab-new'))
}

describe('hub create button', () => {
  it.each(THEMES.map(t => [t]))('%s: is disabled without a folder, enabled once one is chosen', async (theme) => {
    open(theme as Theme)
    const button = await screen.findByTestId('hub-create')
    await waitFor(() => expect(screen.getByRole('radio', { name: /^3D/ }).getAttribute('aria-checked')).toBe('true'))
    expect(button.getAttribute('aria-disabled')).toBe('true')
    expect(button.style.opacity).toBe('0.5')
    fireEvent.click(screen.getByTestId('hub-folder'))
    await waitFor(() => expect(screen.getByTestId('hub-create').getAttribute('aria-disabled')).toBeNull())
    expect(screen.getByTestId('hub-create').style.opacity).toBe('')
  })

  it('a press while it is off creates nothing and names the missing folder', async () => {
    open()
    fireEvent.change(await screen.findByTestId('hub-name'), { target: { value: 'Game' } })
    fireEvent.click(screen.getByTestId('hub-create'))
    expect((await screen.findByTestId('hub-folder-note')).textContent).toBe(en['hub.folderMissing'])
    expect(mocks.invoke).not.toHaveBeenCalledWith('hub-create-project', expect.anything())
  })
})
