import { describe, it, expect, vi } from 'vitest'
import { createConnectors, resolveAppExecutable } from '../main/helpers/connectors'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'

const VIEW = { id: 'blender', state: 'ready', app: { download_url: 'https://www.blender.org/download/' } }

function setup(over: { platform?: string; downloadUrl?: string; dialog?: { canceled: boolean; filePaths: string[] }; fail?: number } = {}) {
  const calls: { method: string; url: string; data?: unknown; headers: Record<string, string> }[] = []
  const http = {
    request: vi.fn(async (config: any) => {
      calls.push(config)
      if (over.fail) throw Object.assign(new Error('boom'), { response: { status: over.fail, data: { detail: 'already running' } } })
      if (config.url.endsWith('/connectors')) {
        return { data: { connectors: [{ ...VIEW, app: { download_url: over.downloadUrl ?? VIEW.app.download_url } }] } }
      }
      return { data: VIEW }
    }),
  }
  const openExternal = vi.fn(async (_url: string) => {})
  const showOpenDialog = vi.fn(async (_options: Record<string, unknown>) => over.dialog ?? { canceled: false, filePaths: ['C:\\Blender\\blender.exe'] })
  const host = createConnectors({
    platform: over.platform ?? 'win32', baseUrl: () => 'http://127.0.0.1:1', appToken: 'tok', uiSecret: 'sec',
    http, dialog: { showOpenDialog }, shell: { openExternal },
  })
  return { host, calls, openExternal, showOpenDialog }
}

describe('connectors ipc · channel', () => {
  it('is on the allow-list', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('connectors')).toBe(true)
  })
})

describe('connectors ipc · op validation', () => {
  it('rejects a non-object request, an unknown op and a bad id without calling the backend', async () => {
    const { host, calls } = setup()
    expect(await host.invoke(null)).toEqual({ error: 'bad_request' })
    expect(await host.invoke({ op: 'format_disk', id: 'blender' })).toEqual({ error: 'unknown_op' })
    expect(await host.invoke({ op: 'plan' })).toEqual({ error: 'bad_id' })
    expect(await host.invoke({ op: 'plan', id: '../../etc' })).toEqual({ error: 'bad_id' })
    expect(await host.invoke({ op: 'plan', id: 42 })).toEqual({ error: 'bad_id' })
    expect(calls).toHaveLength(0)
  })

  it('set_app_path accepts a string or null and nothing else', async () => {
    const { host, calls } = setup()
    for (const body of [undefined, {}, { path: 5 }, { path: '' }, { path: 'x'.repeat(2000) }]) {
      expect(await host.invoke({ op: 'set_app_path', id: 'blender', body })).toEqual({ error: 'bad_path' })
    }
    expect(calls).toHaveLength(0)
    await host.invoke({ op: 'set_app_path', id: 'blender', body: { path: 'C:\\b\\blender.exe' } })
    await host.invoke({ op: 'set_app_path', id: 'blender', body: { path: null } })
    expect(calls.map(c => [c.method, c.url, c.data])).toEqual([
      ['PUT', 'http://127.0.0.1:1/connectors/blender/app-path', { path: 'C:\\b\\blender.exe' }],
      ['PUT', 'http://127.0.0.1:1/connectors/blender/app-path', { path: null }],
    ])
  })

  it('maps every op to its route', async () => {
    const { host, calls } = setup()
    for (const op of ['list', 'plan', 'install', 'install_status', 'cancel', 'remove', 'enable', 'disable']) {
      await host.invoke({ op, id: 'blender' })
    }
    expect(calls.map(c => `${c.method} ${c.url.replace('http://127.0.0.1:1', '')}`)).toEqual([
      'GET /connectors', 'POST /connectors/blender/plan', 'POST /connectors/blender/install',
      'GET /connectors/blender/install', 'POST /connectors/blender/install/cancel', 'POST /connectors/blender/remove',
      'PUT /connectors/blender/enabled', 'PUT /connectors/blender/enabled',
    ])
    expect(calls[6].data).toEqual({ enabled: true })
    expect(calls[7].data).toEqual({ enabled: false })
  })

  it('install forwards replace_addon only when it is exactly true', async () => {
    const { host, calls } = setup()
    await host.invoke({ op: 'install', id: 'blender', body: { replace_addon: true } })
    await host.invoke({ op: 'install', id: 'blender', body: { replace_addon: 'yes' } })
    await host.invoke({ op: 'install', id: 'blender' })
    expect(calls.map(c => c.data)).toEqual([{ replace_addon: true }, undefined, undefined])
  })

  it('returns the backend refusal as an error object with its status', async () => {
    const { host } = setup({ fail: 409 })
    expect(await host.invoke({ op: 'install', id: 'blender' })).toEqual({ error: 'already running', status: 409 })
  })
})

describe('connectors ipc · UI secret', () => {
  it('is added on writes only, never on reads', async () => {
    const { host, calls } = setup()
    for (const op of ['list', 'install_status']) await host.invoke({ op, id: 'blender' })
    for (const op of ['plan', 'install', 'cancel', 'remove', 'enable', 'disable']) await host.invoke({ op, id: 'blender' })
    const reads = calls.filter(c => c.method === 'GET')
    const writes = calls.filter(c => c.method !== 'GET')
    expect(reads).toHaveLength(2)
    expect(writes).toHaveLength(6)
    for (const c of reads) {
      expect(c.headers['X-Session-Token']).toBe('tok')
      expect(c.headers['X-Gamachine-UI-Secret']).toBeUndefined()
    }
    for (const c of writes) {
      expect(c.headers['X-Session-Token']).toBe('tok')
      expect(c.headers['X-Gamachine-UI-Secret']).toBe('sec')
    }
  })
})

describe('connectors ipc · download page', () => {
  it('opens the catalog address when it is on blender.org', async () => {
    const { host, openExternal } = setup()
    expect(await host.invoke({ op: 'open_download', id: 'blender' })).toEqual({ opened: true })
    expect(openExternal).toHaveBeenCalledWith('https://www.blender.org/download/')
  })

  it.each(['http://www.blender.org/download/', 'https://www.blender.org.evil.example/', 'https://evil.example/https://www.blender.org/', 'file:///C:/x', ''])(
    'refuses %s', async url => {
      const { host, openExternal } = setup({ downloadUrl: url })
      expect(await host.invoke({ op: 'open_download', id: 'blender' })).toEqual({ error: 'download_url_refused' })
      expect(openExternal).not.toHaveBeenCalled()
    })

  it('never takes an address from the renderer', async () => {
    const { host, openExternal } = setup()
    await host.invoke({ op: 'open_download', id: 'blender', body: { url: 'https://evil.example/' } })
    expect(openExternal).toHaveBeenCalledTimes(1)
    expect(openExternal).toHaveBeenCalledWith('https://www.blender.org/download/')
  })
})

describe('connectors ipc · choose_app_path', () => {
  it('Windows: filters on blender.exe, then sets the picked path', async () => {
    const { host, calls, showOpenDialog } = setup()
    await host.invoke({ op: 'choose_app_path', id: 'blender' })
    expect(showOpenDialog.mock.calls[0][0].filters).toEqual([{ name: 'Blender', extensions: ['exe'] }])
    expect(calls[0]).toMatchObject({ method: 'PUT', data: { path: 'C:\\Blender\\blender.exe' } })
    expect(calls[0].headers['X-Gamachine-UI-Secret']).toBe('sec')
  })

  it('macOS: a .app bundle resolves to Contents/MacOS/Blender', async () => {
    expect(resolveAppExecutable('/Applications/Blender.app', 'darwin').replace(/\\/g, '/')).toBe('/Applications/Blender.app/Contents/MacOS/Blender')
    expect(resolveAppExecutable('/opt/blender/blender', 'linux')).toBe('/opt/blender/blender')
    const { host, calls } = setup({ platform: 'darwin', dialog: { canceled: false, filePaths: ['/Applications/Blender.app'] } })
    await host.invoke({ op: 'choose_app_path', id: 'blender' })
    expect((calls[0].data as { path: string }).path.replace(/\\/g, '/')).toBe('/Applications/Blender.app/Contents/MacOS/Blender')
  })

  it('a closed dialog changes nothing', async () => {
    const { host, calls } = setup({ dialog: { canceled: true, filePaths: [] } })
    expect(await host.invoke({ op: 'choose_app_path', id: 'blender' })).toEqual({ cancelled: true })
    expect(calls).toHaveLength(0)
  })
})
