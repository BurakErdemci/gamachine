/**
 * Fixes after the blind check of the Unity first-time flows (12 Nov 2026):
 * - the launch toast's "Install Unity X" reads the unsaved-file state when it is clicked, and closes the workspace
 *   only when it is the toast's project;
 * - a project screen that is already up takes an install request at once;
 * - the Hierarchy panel's Connect only connects (through the function the page really calls);
 * - Turkish wording of the API key placeholder and the plan name.
 */
import React from 'react'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => {
  const get = vi.fn(); const post = vi.fn(); const put = vi.fn()
  return { default: { get, post, put, defaults: { headers: { common: {} } } }, get, post, put }
})

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { connectUnityMcp } from '../renderer/hooks/home/useAIConfig'
import * as hub from '../renderer/lib/hub'
import { planName } from '../renderer/lib/modelText'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'

const tEn = (k: any, v?: any) => ceviriUygula('en', k, v)
const MISSING = '6000.4.4f1'
const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')

beforeEach(() => {
  hub.resetHubStore()
  hub.resetInstallStore()
  hub.takeInstallRequest()
  mocks.invoke.mockReset().mockImplementation(async (channel: string, arg: any) => {
    if (channel === 'host-workspace-path') return arg
    if (channel === 'backend-workspace-path') return arg
    if (channel === 'workspace-info') return arg.map((p: string) => ({ path: p, status: 'ok', unityVersion: MISSING }))
    if (channel === 'hub-list-editors') return { editors: [] }
    if (channel === 'hub-list-templates') return { templates: [] }
    if (channel === 'hub-install-catalog') {
      return { ok: true, cli: { available: true }, releases: [{
        version: MISSING, changeset: 'abc', stream: 'LTS', releaseDate: '2026-10-30T16:57:42.531Z', recommended: true, installed: false,
        downloadBytes: 100, installedBytes: 200, modules: [{ id: 'webgl', name: 'webgl', category: 'PLATFORM', downloadBytes: 10, installedBytes: 20, preSelected: false, eulas: [], parts: [], installed: false }],
      }] }
    }
    if (channel === 'hub-install-plan') return { ok: true, installRoot: 'C:\\U', downloadBytes: 1, installedBytes: 2, freeBytes: 1e12, enough: true, eulas: [] }
    return null
  })
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [] } })
  vi.mocked(axios.post).mockReset()
})
afterEach(() => cleanup())

// --- the launch toast's install action ---

const makeDeps = (over: Partial<hub.InstallToastDeps> = {}) => ({
  toastPath: 'C:/Projects/Deneme',
  workspacePath: () => 'C:\\Projects\\Deneme' as string | null,
  isDirty: () => false,
  confirmDiscard: vi.fn(async () => true),
  closeWorkspace: vi.fn(),
  ...over,
})

describe('the launch toast install action', () => {
  it('asks about unsaved edits made after the toast was shown, as of the click', async () => {
    // Clean when the toast (and the render that made it) existed, edited before the click.
    const state = { dirty: false }
    const deps = makeDeps({ isDirty: () => state.dirty, confirmDiscard: vi.fn(async () => false) })
    state.dirty = true
    await hub.installFromLaunchToast(MISSING, deps)
    expect(deps.confirmDiscard).toHaveBeenCalledTimes(1)
    expect(deps.closeWorkspace).not.toHaveBeenCalled()
    expect(hub.takeInstallRequest()).toBe('')
  })

  it('closes the workspace and hands over the version once the edits may go', async () => {
    const deps = makeDeps({ isDirty: () => true })
    await hub.installFromLaunchToast(MISSING, deps)
    expect(deps.confirmDiscard).toHaveBeenCalledTimes(1)
    expect(deps.closeWorkspace).toHaveBeenCalledTimes(1)
    expect(hub.takeInstallRequest()).toBe(MISSING)
  })

  it('does not ask when nothing is unsaved', async () => {
    const deps = makeDeps()
    await hub.installFromLaunchToast(MISSING, deps)
    expect(deps.confirmDiscard).not.toHaveBeenCalled()
    expect(deps.closeWorkspace).toHaveBeenCalledTimes(1)
  })

  it('leaves another project, and its unsaved edits, alone', async () => {
    const deps = makeDeps({ workspacePath: () => 'C:/Projects/Other', isDirty: () => true })
    await hub.installFromLaunchToast(MISSING, deps)
    expect(deps.confirmDiscard).not.toHaveBeenCalled()
    expect(deps.closeWorkspace).not.toHaveBeenCalled()
    expect(hub.takeInstallRequest()).toBe(MISSING)
  })

  it('has nothing to close when the workspace was closed meanwhile', async () => {
    const deps = makeDeps({ workspacePath: () => null })
    await hub.installFromLaunchToast(MISSING, deps)
    expect(deps.closeWorkspace).not.toHaveBeenCalled()
    expect(hub.takeInstallRequest()).toBe(MISSING)
  })

  it('the page reads the workspace and the buffer at click time and goes through this function', () => {
    expect(home).toMatch(/installFromLaunchToast\(version, \{/)
    expect(home).toMatch(/isDirty: \(\) => fsNow\.current\.isDirty/)
    expect(home).toMatch(/workspacePath: \(\) => fsNow\.current\.workspacePath/)
    expect(home).toMatch(/launchWithToast\(fs\.workspacePath, undefined, showToast, t, installFromTopBar\(fs\.workspacePath\)\)/)
  })

  it('a project screen that is already up opens the wizard at that version when asked', async () => {
    render(
      <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: tEn }}>
        <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'B', sessionToken: 'tok' } as any} userName="B"
          onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
      </LangContext.Provider>,
    )
    expect(screen.queryByTestId('hub-install-modules')).toBeNull()
    await hub.installFromLaunchToast(MISSING, makeDeps({ workspacePath: () => null }))
    await screen.findByTestId('hub-install-modules')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toContain(MISSING)
  })
})

// --- the Hierarchy panel's Connect ---

describe('hierarchy panel Connect through the page function', () => {
  it.each(['starting', 'running', 'connected', 'unknown'] as const)('does not toggle while the link is %s', (status) => {
    const toggle = vi.fn()
    connectUnityMcp(status, toggle)
    expect(toggle).not.toHaveBeenCalled()
  })

  it.each(['off', 'blocked'] as const)('toggles once when the link is %s', (status) => {
    const toggle = vi.fn()
    connectUnityMcp(status, toggle)
    expect(toggle).toHaveBeenCalledTimes(1)
  })
})

// --- Turkish wording ---

describe('Turkish settings wording', () => {
  it('asks for the API key in plain Turkish', () => {
    expect(translations.tr['settings.apiKeyPlaceholder']).toBe('API anahtarını gir…')
  })

  it('writes the subscription plan as a name', () => {
    expect(ceviriUygula('tr', 'set.cli.plan', { plan: planName('plus') })).toBe('Plus planı')
    expect(ceviriUygula('en', 'mm.sub.subscriptionPlan', { plan: planName('plus') })).toBe('Subscription · Plus')
    expect(planName(undefined)).toBe('')
  })

  it('both places that show a plan use that name', () => {
    for (const file of ['components/home/settings/ModelsPage.tsx', 'components/home/ModelSelector.tsx']) {
      expect(readFileSync(resolve(__dirname, '../renderer', file), 'utf8')).toMatch(/plan: planName\(fam\.plan\)/)
    }
  })
})
