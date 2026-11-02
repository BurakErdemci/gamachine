import path from 'path'
import type { HttpClient } from './remote-control'

/**
 * Connectors (Settings > Bağlayıcılar): one IPC channel, `connectors`, in front of the backend's
 * /connectors routes. The renderer sends `{ op, id, body }`; the path, the method and the UI secret
 * are fixed here. The secret goes on every call that is not a read, never on GET.
 */

const ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/
const MAX_PATH_LENGTH = 1024
// The only page the "download the app" buttons may open.
const DOWNLOAD_PREFIX = 'https://www.blender.org/'

export const CONNECTOR_OPS = [
  'list', 'plan', 'install', 'install_status', 'cancel', 'remove', 'enable', 'disable',
  'set_app_path', 'choose_app_path', 'open_download',
] as const
type Op = typeof CONNECTOR_OPS[number]

export interface ConnectorsDeps {
  platform: string
  baseUrl: () => string
  appToken: string
  uiSecret: string
  http: HttpClient
  dialog: { showOpenDialog(options: Record<string, unknown>): Promise<{ canceled: boolean; filePaths: string[] }> }
  shell: { openExternal(url: string): Promise<void> }
  log?: (...args: unknown[]) => void
}

export interface ConnectorsError { error: string; status?: number }

const fail = (error: string, status?: number): ConnectorsError => (status === undefined ? { error } : { error, status })

/** A macOS .app bundle is a folder; the executable the backend can run sits inside it. */
export function resolveAppExecutable(picked: string, platform: string): string {
  if (platform === 'darwin' && picked.toLowerCase().endsWith('.app')) return path.join(picked, 'Contents', 'MacOS', 'Blender')
  return picked
}

export function createConnectors(deps: ConnectorsDeps) {
  const log = deps.log ?? (() => {})

  async function request(method: 'GET' | 'POST' | 'PUT', route: string, data?: unknown): Promise<any> {
    const headers: Record<string, string> = { 'X-Session-Token': deps.appToken }
    if (method !== 'GET') headers['X-Gamachine-UI-Secret'] = deps.uiSecret
    try {
      const response = await deps.http.request({ method, url: `${deps.baseUrl()}${route}`, data, headers, timeout: 15_000 })
      return response.data
    } catch (e) {
      const err = e as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
      const detail = err?.response?.data?.detail
      log('[connectors] request failed:', method, route, err?.response?.status, err?.message)
      return fail(typeof detail === 'string' && detail ? detail : 'request_failed', err?.response?.status)
    }
  }

  const isFailure = (v: unknown): v is ConnectorsError => !!v && typeof v === 'object' && typeof (v as ConnectorsError).error === 'string'

  async function setAppPath(id: string, value: string | null) {
    return request('PUT', `/connectors/${id}/app-path`, { path: value })
  }

  async function chooseAppPath(id: string) {
    const options: Record<string, unknown> = { properties: ['openFile'] }
    if (deps.platform === 'win32') options.filters = [{ name: 'Blender', extensions: ['exe'] }]
    if (deps.platform === 'darwin') options.filters = [{ name: 'Blender', extensions: ['app'] }]
    const result = await deps.dialog.showOpenDialog(options)
    if (result.canceled || !result.filePaths?.[0]) return { cancelled: true }
    return setAppPath(id, resolveAppExecutable(result.filePaths[0], deps.platform))
  }

  async function openDownload(id: string) {
    const list = await request('GET', '/connectors')
    if (isFailure(list)) return list
    const view = Array.isArray(list?.connectors) ? list.connectors.find((c: { id?: unknown }) => c?.id === id) : undefined
    const url = view?.app?.download_url
    if (typeof url !== 'string' || !url.startsWith(DOWNLOAD_PREFIX)) return fail('download_url_refused')
    try {
      await deps.shell.openExternal(url)
      return { opened: true }
    } catch (e) {
      log('[connectors] openExternal failed:', (e as Error)?.message)
      return fail('open_failed')
    }
  }

  return {
    async invoke(req: unknown): Promise<any> {
      if (!req || typeof req !== 'object') return fail('bad_request')
      const { op, id, body } = req as { op?: unknown; id?: unknown; body?: unknown }
      if (typeof op !== 'string' || !(CONNECTOR_OPS as readonly string[]).includes(op)) return fail('unknown_op')
      if (op === 'list') return request('GET', '/connectors')
      if (typeof id !== 'string' || !ID_PATTERN.test(id)) return fail('bad_id')
      switch (op as Exclude<Op, 'list'>) {
        case 'plan': return request('POST', `/connectors/${id}/plan`)
        case 'install': return request('POST', `/connectors/${id}/install`,
          (body as { replace_addon?: unknown } | undefined)?.replace_addon === true ? { replace_addon: true } : undefined)
        case 'install_status': return request('GET', `/connectors/${id}/install`)
        case 'cancel': return request('POST', `/connectors/${id}/install/cancel`)
        case 'remove': return request('POST', `/connectors/${id}/remove`)
        case 'enable': return request('PUT', `/connectors/${id}/enabled`, { enabled: true })
        case 'disable': return request('PUT', `/connectors/${id}/enabled`, { enabled: false })
        case 'set_app_path': {
          const value = (body as { path?: unknown } | undefined)?.path
          if (value !== null && (typeof value !== 'string' || !value || value.length > MAX_PATH_LENGTH)) return fail('bad_path')
          return setAppPath(id, value as string | null)
        }
        case 'choose_app_path': return chooseAppPath(id)
        case 'open_download': return openDownload(id)
      }
    },
  }
}
