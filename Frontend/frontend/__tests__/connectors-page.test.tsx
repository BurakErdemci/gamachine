import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'

const confirm = vi.hoisted(() => ({ answer: true, calls: [] as string[] }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({
  confirmDialog: vi.fn(async (message: string) => { confirm.calls.push(message); return confirm.answer }),
}))

import { ConnectorsPage } from '../renderer/components/home/settings/ConnectorsPage'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const view = (over: Record<string, unknown> = {}) => ({
  id: 'blender', name: 'Blender',
  summary: { tr: '3B modeli Blender\'da yapar.', en: 'Builds 3D models in Blender.' },
  capabilities: { tr: ['Sahneyi okur'], en: ['Reads the scene'] },
  risk: { tr: 'Kod çalıştırır.', en: 'Runs code.' },
  state: 'not_installed', enabled: false, connected: false,
  app: { found: [{ version: '5.2.0', path: 'C:\\B\\blender.exe' }], chosen: { version: '5.2.0', path: 'C:\\B\\blender.exe' },
    custom_path: null, min_version: '4.2', download_url: 'https://www.blender.org/download/' },
  installed: null, error: null,
  ...over,
}) as any

const PLAN = { download_mb: 180, estimated_s: 150, target: 'C:\\Users\\x\\Gamachine\\connectors\\blender', steps: ['python', 'package', 'addon', 'check'], replace_addon: null }
const job = (over: Record<string, unknown> = {}) => ({
  id: 'j1', state: 'running', step: 'python', steps_done: [], downloaded_mb: null, started_at: Math.floor(Date.now() / 1000),
  error: null, ...over,
}) as any

interface Fake { view: any; job: any; plan: any; ops: { op: string; id?: string; body?: unknown }[]; installBody?: unknown }
function install(fake: Fake) {
  const invoke = vi.fn(async (_channel: string, req: { op: string; id?: string; body?: unknown }) => {
    fake.ops.push(req)
    switch (req.op) {
      case 'list': return { connectors: [fake.view] }
      case 'plan': return fake.plan
      case 'install': fake.installBody = req.body; fake.job = job(); fake.view = { ...fake.view, state: 'installing' }; return { job: fake.job }
      case 'install_status': return { job: fake.job }
      case 'cancel': fake.job = null; fake.view = { ...fake.view, state: 'not_installed' }; return { cancelled: true }
      case 'enable': fake.view = { ...fake.view, enabled: true }; return fake.view
      case 'open_download': return { opened: true }
      default: return { error: 'unknown_op' }
    }
  })
  ;(window as any).ipc = { invoke }
  return invoke
}
const fake = (over: Partial<Fake> = {}): Fake => ({ view: view(), job: null, plan: PLAN, ops: [], ...over })

const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }
const mount = async () => {
  const r = render(<ConnectorsPage saved={vi.fn()} showToast={vi.fn()} />)
  await flush()
  return r
}
const primary = () => screen.queryByTestId('conn-primary-blender')

beforeEach(() => {
  aktifDilAyarla('tr')
  confirm.answer = true
  confirm.calls = []
  vi.useFakeTimers()
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.useRealTimers()
})

describe('connectors page · one primary action per state', () => {
  const cases: [string, Record<string, unknown>, string | null, string | null][] = [
    ['app_missing', { state: 'app_missing', app: { found: [], chosen: null, custom_path: null, min_version: '4.2', download_url: 'https://www.blender.org/download/' } }, 'download', "Blender'ı indir"],
    ['app_too_old', { state: 'app_too_old' }, 'downloadSupported', 'Desteklenen sürümü indir'],
    ['not_installed', { state: 'not_installed' }, 'install', 'Kur'],
    ['update_available', { state: 'update_available', installed: { version: '2.2.5', addon_owner: 'gamachine' } }, 'update', 'Güncelle'],
    ['error', { state: 'error', error: { code: 'x', message: 'Disk full' } }, 'retry', 'Tekrar dene'],
    ['remove_incomplete', { state: 'error', error: { code: 'remove_incomplete', message: 'still there: x' } }, 'remove', 'Kaldır'],
    ['ready', { state: 'ready', installed: { version: '2.2.6', addon_owner: 'gamachine' } }, null, null],
  ]
  it.each(cases)('%s', async (_name, over, action, label) => {
    install(fake({ view: view(over) }))
    await mount()
    if (action === null) expect(primary()).toBeNull()
    else {
      expect(primary()!.getAttribute('data-action')).toBe(action)
      expect(primary()!.textContent).toBe(label)
    }
    // Exactly one primary-styled button on the card.
    expect(document.querySelectorAll('.btn-primary')).toHaveLength(action === null ? 0 : 1)
  })

  it('the download buttons ask main to open the page (no address from the renderer)', async () => {
    const f = fake({ view: view({ state: 'app_missing' }) })
    install(f)
    await mount()
    fireEvent.click(primary()!)
    await flush()
    expect(f.ops.at(-1)).toEqual({ op: 'open_download', id: 'blender', body: undefined })
  })

  it('an error state shows its message', async () => {
    install(fake({ view: view({ state: 'error', error: { code: 'x', message: 'Disk full' } }) }))
    await mount()
    expect(screen.getByTestId('conn-status-blender').textContent).toContain('Disk full')
  })

  it('a connected, enabled connector shows the Bağlı badge', async () => {
    install(fake({ view: view({ state: 'ready', enabled: true, connected: true, installed: { version: '2.2.6' } }) }))
    await mount()
    expect(screen.getByTestId('conn-connected-blender').textContent).toBe('Bağlı')
  })

  it('the status line names the app version that was found', async () => {
    install(fake())
    await mount()
    expect(screen.getByTestId('conn-status-blender').textContent).toContain('Blender 5.2.0 bulundu')
  })
})

describe('connectors page · switch', () => {
  it.each(['app_missing', 'app_too_old', 'not_installed', 'installing', 'error'])('is disabled in %s', async state => {
    install(fake({ view: view({ state }) }))
    await mount()
    expect((screen.getByTestId('conn-switch-blender') as HTMLButtonElement).disabled).toBe(true)
  })

  it.each(['ready', 'update_available'])('is usable in %s and turns the connector on', async state => {
    const f = fake({ view: view({ state, installed: { version: '2.2.6' } }) })
    install(f)
    await mount()
    const sw = screen.getByTestId('conn-switch-blender') as HTMLButtonElement
    expect(sw.disabled).toBe(false)
    fireEvent.click(sw)
    await flush()
    expect(f.ops.some(o => o.op === 'enable')).toBe(true)
    expect(screen.getByTestId('conn-switch-blender').getAttribute('aria-checked')).toBe('true')
  })

  it('carries the guided-tour anchor on the Blender card', async () => {
    install(fake())
    await mount()
    expect(screen.getByTestId('conn-switch-blender').parentElement!.getAttribute('data-guide')).toBe('integration-switch-blender')
  })
})

describe('connectors page · details', () => {
  it('lists capabilities, the risk line and a version picker for several versions', async () => {
    const f = fake({ view: view({
      state: 'ready', installed: { version: '2.2.6', addon_owner: 'gamachine' },
      app: { found: [{ version: '5.2.0', path: 'A' }, { version: '4.5.0', path: 'B' }], chosen: { version: '5.2.0', path: 'A' }, custom_path: null, min_version: '4.2', download_url: 'https://www.blender.org/download/' },
    }) })
    install(f)
    await mount()
    expect(screen.getByTestId('conn-caps-blender').textContent).toContain('Sahneyi okur')
    expect(screen.getByTestId('conn-risk-blender').textContent).toContain('Kod çalıştırır.')
    expect(screen.getByTestId('conn-picker-blender')).toBeTruthy()
    expect(screen.getByTestId('conn-repair-blender')).toBeTruthy()
    expect(screen.queryByTestId('conn-addon-user-blender')).toBeNull()
  })

  it('tells that the Blender add-on belongs to another tool and Remove leaves it', async () => {
    install(fake({ view: view({ state: 'ready', installed: { version: '2.2.6', addon_owner: 'user' } }) }))
    await mount()
    expect(screen.getByTestId('conn-addon-user-blender').textContent).toBe(cevir('conn.addonUser'))
    expect(screen.getByTestId('conn-remove-blender')).toBeTruthy()
  })
})

describe('connectors page · install flow', () => {
  const open = async (f: Fake) => {
    install(f)
    await mount()
    fireEvent.click(primary()!)
    await flush()
  }

  it('asks first: the plan shows size, time and target and nothing is installed yet', async () => {
    const f = fake()
    await open(f)
    expect(screen.getByTestId('conn-plan-size-blender').textContent).toContain('180 MB')
    expect(screen.getByTestId('conn-plan-time-blender').textContent).toContain('2:30')
    expect(screen.getByTestId('conn-plan-target-blender').textContent).toContain(PLAN.target)
    expect(f.ops.some(o => o.op === 'install')).toBe(false)
    expect(screen.getByTestId('conn-plan-go-blender').textContent).toBe('Kur')
    expect(screen.getByTestId('conn-plan-cancel-blender').textContent).toBe('Vazgeç')
    expect(screen.queryByTestId('conn-plan-replace-blender')).toBeNull()
  })

  it('Vazgeç closes the plan without installing', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-cancel-blender'))
    await flush()
    expect(screen.queryByTestId('conn-plan-blender')).toBeNull()
    expect(f.ops.some(o => o.op === 'install')).toBe(false)
  })

  it('without an add-on conflict the install body carries no replace_addon', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    expect(f.installBody).toBeUndefined()
  })

  it('a foreign incompatible add-on adds the replace line and the install sends replace_addon', async () => {
    const f = fake({ plan: { ...PLAN, replace_addon: { path: 'C:\\addons\\blender_mcp.py', found_protocol: 1, needed_protocol: 2 } } })
    await open(f)
    expect(screen.getByTestId('conn-plan-replace-blender').textContent).toBe(cevir('conn.plan.replaceAddon'))
    expect(screen.getByTestId('conn-plan-go-blender').textContent).toBe('Kur')
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    expect(f.installBody).toEqual({ replace_addon: true })
  })

  it('progress steps advance as polled jobs change, with elapsed time and downloaded MB', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    const status = (step: string) => screen.getByTestId(`conn-step-blender-${step}`).getAttribute('data-status')
    expect(status('python')).toBe('current')
    expect(status('package')).toBe('pending')
    f.job = job({ step: 'package', steps_done: ['python'], downloaded_mb: 42 })
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(status('python')).toBe('done')
    expect(status('package')).toBe('current')
    expect(screen.getByTestId('conn-downloaded-blender').textContent).toContain('42 / 180 MB')
    expect(screen.getByTestId('conn-time-blender').textContent).toContain('2:30')
    f.job = job({ step: 'check', steps_done: ['python', 'package', 'addon'] })
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(status('addon')).toBe('done')
    expect(status('check')).toBe('current')
  })

  it('finishing shows the success line', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    f.job = job({ state: 'done', steps_done: ['python', 'package', 'addon', 'check'] })
    f.view = { ...f.view, state: 'ready', installed: { version: '2.2.6' } }
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByTestId('conn-done-blender')).toBeTruthy()
    expect(screen.queryByTestId('conn-progress-blender')).toBeNull()
  })

  it('cancel asks to confirm and then cancels; declining keeps the job', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    confirm.answer = false
    fireEvent.click(screen.getByTestId('conn-cancel-blender'))
    await flush()
    expect(confirm.calls).toHaveLength(1)
    expect(f.ops.some(o => o.op === 'cancel')).toBe(false)
    confirm.answer = true
    fireEvent.click(screen.getByTestId('conn-cancel-blender'))
    await flush()
    expect(f.ops.some(o => o.op === 'cancel')).toBe(true)
    expect(screen.queryByTestId('conn-progress-blender')).toBeNull()
    expect(primary()!.getAttribute('data-action')).toBe('install')
  })

  it('a failed job shows its message once, with one Tekrar dene that opens the plan again', async () => {
    const f = fake()
    await open(f)
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    f.job = job({ state: 'failed', error: { code: 'net', message: 'Network lost', retryable: true } })
    f.view = { ...f.view, state: 'error', error: { code: 'net', message: 'Network lost' } }
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByTestId('conn-failed-blender').textContent).toContain('Network lost')
    expect(screen.getAllByRole('button', { name: 'Tekrar dene' })).toHaveLength(1)
    expect(screen.queryByTestId('conn-retry-blender')).toBeNull()
    const installs = () => f.ops.filter(o => o.op === 'install').length
    expect(installs()).toBe(1)
    fireEvent.click(primary()!)
    await flush()
    expect(screen.getByTestId('conn-plan-blender')).toBeTruthy()
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    expect(installs()).toBe(2)
    expect(screen.getByTestId('conn-progress-blender')).toBeTruthy()
  })

  it('a broken install: the primary Tekrar dene opens the repair plan', async () => {
    const f = fake({ view: view({ state: 'error', installed: { version: '2.2.6', addon_owner: 'gamachine' },
      error: { code: 'broken_install', message: 'The installed package is missing files.' } }) })
    await open(f)
    expect(screen.getByTestId('conn-plan-blender')).toBeTruthy()
    expect(f.ops.some(o => o.op === 'plan')).toBe(true)
    expect(f.ops.some(o => o.op === 'install')).toBe(false)
  })

  it('a failed repair of a working install keeps it ready, with the job error and one Tekrar dene', async () => {
    const f = fake({ view: view({ state: 'ready', installed: { version: '2.2.6', addon_owner: 'gamachine' } }),
      job: job({ state: 'failed', error: { code: 'package_failed', message: 'boom', retryable: true } }) })
    install(f)
    await mount()
    expect(primary()).toBeNull()
    expect(screen.getByTestId('conn-failed-blender').textContent).toContain('boom')
    expect(screen.getAllByRole('button', { name: 'Tekrar dene' })).toHaveLength(1)
    fireEvent.click(screen.getByTestId('conn-retry-blender'))
    await flush()
    expect(f.ops.filter(o => o.op === 'install').length).toBe(1)
  })

  it('a file of another add-on under our name gets its own replace line', async () => {
    const f = fake({ plan: { ...PLAN, replace_addon: { path: 'C:\\addons\\blender_mcp.py', found_protocol: null, needed_protocol: 15 } } })
    await open(f)
    expect(screen.getByTestId('conn-plan-replace-blender').textContent).toBe(cevir('conn.plan.replaceFile'))
    fireEvent.click(screen.getByTestId('conn-plan-go-blender'))
    await flush()
    expect(f.installBody).toEqual({ replace_addon: true })
  })

  it('a Remove that left files reloads the card, which shows what is left', async () => {
    const f = fake({ view: view({ state: 'ready', installed: { version: '2.2.6', addon_owner: 'gamachine' } }) })
    const invoke = install(f)
    const base = invoke.getMockImplementation()!
    invoke.mockImplementation(async (channel: string, req: { op: string }) => {
      if (req.op === 'remove') {
        f.ops.push(req)
        f.view = { ...f.view, state: 'error', error: { code: 'remove_incomplete', message: 'Remove did not finish; still there: x' } }
        return { error: 'http_409' }
      }
      return base(channel, req)
    })
    await mount()
    fireEvent.click(screen.getByTestId('conn-remove-blender'))
    await flush()
    await flush()
    expect(f.ops.filter(o => o.op === 'list').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('conn-status-blender').textContent).toContain('still there: x')
  })

  it('an incomplete Remove without an install record offers Remove again, not the install plan', async () => {
    const f = fake({ view: view({ state: 'error', installed: null,
      error: { code: 'remove_incomplete', message: 'Remove did not finish; still there: not deleted: C:/x' } }) })
    const invoke = install(f)
    const base = invoke.getMockImplementation()!
    invoke.mockImplementation(async (channel: string, req: { op: string }) => {
      if (req.op === 'remove') {
        f.ops.push(req)
        f.view = { ...f.view, state: 'not_installed', error: null }
        return f.view
      }
      return base(channel, req)
    })
    await mount()
    expect(screen.getByTestId('conn-remove-blender')).toBeTruthy()
    expect(screen.queryByTestId('conn-repair-blender')).toBeNull()
    expect(primary()!.getAttribute('data-action')).toBe('remove')
    fireEvent.click(primary()!)
    await flush()
    await flush()
    expect(confirm.calls).toHaveLength(1)
    expect(f.ops.filter(o => o.op === 'remove')).toHaveLength(1)
    expect(f.ops.some(o => o.op === 'plan')).toBe(false)
    expect(primary()!.getAttribute('data-action')).toBe('install')
  })

  it('leaving and coming back resumes the running job from the backend', async () => {
    const f = fake({ view: view({ state: 'installing' }), job: job({ step: 'package', steps_done: ['python'], downloaded_mb: 10 }) })
    install(f)
    const first = await mount()
    expect(screen.getByTestId('conn-progress-blender')).toBeTruthy()
    first.unmount()
    await mount()
    expect(f.ops.filter(o => o.op === 'install_status').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('conn-step-blender-package').getAttribute('data-status')).toBe('current')
    expect(screen.getByTestId('conn-step-blender-python').getAttribute('data-status')).toBe('done')
    expect(f.ops.some(o => o.op === 'install')).toBe(false)
  })
})
