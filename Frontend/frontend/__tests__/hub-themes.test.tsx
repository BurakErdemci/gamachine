import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: { workspaces: [] } })), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { AppearanceContext, DEFAULTS, THEMES, type Theme } from '../renderer/lib/appearance'
import { resetHubStore } from '../renderer/lib/hub'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'

const en = translations.en
let create: () => Promise<any> = () => new Promise(() => {})
const ipc = async (channel: string, arg: any) => {
  if (channel === 'hub-list-editors') return { editors: [{ version: '6000.2.4f1', path: '/U/Unity' }, { version: '2022.3.55f1', path: '/U2/Unity' }] }
  if (channel === 'hub-list-templates') return { templates: [{ id: 'com.unity.template.3d-cross-platform-17.0.6', label: '3d', file: 'a.tgz' }] }
  if (channel === 'hub-pick-parent-dir') return { path: '/Users/me/Games' }
  if (channel === 'hub-create-project') return create()
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok', unityVersion: null }))
  return null
}

// What makes each composition its own, not a recolour of one layout.
const SIGNATURE: Record<Theme, { form: string; run: string; tabs: Record<string, string> }> = {
  arena: { form: '.ar-slot .ar-name', run: '.ar-segs', tabs: { projects: 'hub.tabProjectsQuest', editors: 'hub.tabEditorsQuest', new: 'hub.tabNewQuest' } },
  sade: { form: '.sd-row .sd-radios', run: '.sd-line', tabs: { projects: 'hub.tabProjects', editors: 'hub.tabEditors', new: 'hub.tabNew' } },
  pafta: { form: '.pf-tb .pf-c-go', run: '.pf-ruler .pf-cursor', tabs: { projects: 'hub.tabProjectsNote', editors: 'hub.tabEditorsNote', new: 'hub.tabNewNote' } },
  atolye: { form: '.at-order .at-stub', run: '.at-gauge .at-needle', tabs: { projects: 'hub.tabProjectsShop', editors: 'hub.tabEditorsShop', new: 'hub.tabNewShop' } },
  ozel: { form: '.oz-make .oz-card .brand-head', run: '.oz-card.is-creating', tabs: { projects: 'hub.tabProjects', editors: 'hub.tabEditors', new: 'hub.tabNew' } },
}

const view = (theme: Theme) => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  return render(
    <AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, theme }, setAppearance: () => {} }}>
      <LangContext.Provider value={value}>
        <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'Burak', sessionToken: 'tok' } as any} userName="Burak"
          onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
      </LangContext.Provider>
    </AppearanceContext.Provider>,
  )
}

beforeEach(() => { create = () => new Promise(() => {}); resetHubStore(); mocks.invoke.mockReset().mockImplementation(ipc) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('hub: one composition per theme', () => {
  it('covers every theme the app has', () => {
    expect(Object.keys(SIGNATURE).sort()).toEqual([...THEMES].sort())
  })

  it.each(THEMES.map(t => [t]))('%s draws its own layout with its own words, and the same flow works in it', async (theme) => {
    const { container } = view(theme as Theme)
    const hub = container.querySelector('.hub')!
    expect(hub.getAttribute('data-hub')).toBe(theme)
    const sig = SIGNATURE[theme as Theme]
    for (const [v, key] of Object.entries(sig.tabs)) expect(screen.getByTestId(`hub-tab-${v}`).textContent).toContain(en[key as keyof typeof en])
    fireEvent.click(screen.getByTestId('hub-tab-new'))
    await waitFor(() => expect(container.querySelector(sig.form)).not.toBeNull())
    // Other themes' parts are not in the DOM: the theme picks the component, not a stylesheet.
    for (const [other, s] of Object.entries(SIGNATURE)) if (other !== theme) expect(container.querySelector(s.form)).toBeNull()
    await waitFor(() => expect(screen.getByRole('radio').getAttribute('aria-checked')).toBe('true'))
    fireEvent.change(screen.getByTestId('hub-name'), { target: { value: 'Game' } })
    fireEvent.click(screen.getByTestId('hub-folder'))
    await waitFor(() => expect(screen.getByTestId('hub-folder').textContent).toContain('/Users/me/Games'))
    fireEvent.click(screen.getByTestId('hub-create'))
    await waitFor(() => expect(screen.getByTestId('hub-progress')).toBeTruthy())
    expect(container.querySelector(sig.run)).not.toBeNull()
    expect(screen.getByTestId('hub-cancel')).toBeTruthy()
  })

  it.each(THEMES.map(t => [t]))('%s offers Refresh on a populated editors list', async (theme) => {
    view(theme as Theme)
    fireEvent.click(screen.getByTestId('hub-tab-editors'))
    await screen.findByTestId('hub-editors')
    const before = mocks.invoke.mock.calls.filter(c => c[0] === 'hub-list-editors').length
    fireEvent.click(screen.getByRole('button', { name: en['hub.refresh'] }))
    await waitFor(() => expect(mocks.invoke.mock.calls.filter(c => c[0] === 'hub-list-editors').length).toBe(before + 1))
  })
})

describe('hub: run cards and captions', () => {
  it('the Ozel card describes the run after a remount, not the reset form', async () => {
    view('ozel')
    fireEvent.click(screen.getByTestId('hub-tab-new'))
    await waitFor(() => expect(screen.getByRole('radio').getAttribute('aria-checked')).toBe('true'))
    fireEvent.change(screen.getByTestId('hub-version'), { target: { value: '2022.3.55f1' } })
    await waitFor(() => expect(screen.getByRole('radio').getAttribute('aria-checked')).toBe('true'))
    fireEvent.change(screen.getByTestId('hub-name'), { target: { value: 'Game' } })
    fireEvent.click(screen.getByTestId('hub-folder'))
    await waitFor(() => expect(screen.getByTestId('hub-folder').textContent).toContain('/Users/me/Games'))
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    cleanup()
    view('ozel')
    const card = await screen.findByTestId('hub-ozel-card')
    expect(card.textContent).toContain('3D')
    expect(card.textContent).toContain('2022.3.55f1')
    expect(card.textContent).not.toContain('6000.2.4f1')
    expect(card.textContent).toContain('/Users/me/Games/Game')
    expect(card.textContent).not.toContain(en['hub.folderNone'])
  })

  it('Atolye words its order number through the i18n table in both languages', async () => {
    expect(translations.tr['hub.orderNo']).toBeTruthy()
    expect(translations.en['hub.orderNo']).toBeTruthy()
    const { container } = view('atolye')
    fireEvent.click(screen.getByTestId('hub-tab-new'))
    await waitFor(() => expect(container.querySelector('.at-stub-no')).not.toBeNull())
    expect(container.querySelector('.at-stub-no')!.textContent).toBe(en['hub.orderNo'].replace('{no}', '001'))
  })
})
