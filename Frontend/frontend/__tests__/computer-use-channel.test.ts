import fs from 'fs'
import path from 'path'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerUseHost } from '../main/helpers/computer-use'

const mocks = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => any>(),
  own: vi.fn((event: any) => event?.own === true),
  request: vi.fn(), post: vi.fn(), invoke: vi.fn(), reconcile: vi.fn(async () => {}),
  accessibility: vi.fn(() => true), screen: vi.fn(() => 'granted'), open: vi.fn(async () => {}),
}))
vi.mock('axios', () => ({ default: { request: mocks.request, post: mocks.post } }))
vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '.'), setPath: vi.fn(), requestSingleInstanceLock: () => false, quit: vi.fn(), on: vi.fn() },
  ipcMain: { handle: (name: string, handler: any) => mocks.handlers.set(name, handler) },
  dialog: {}, shell: { openExternal: mocks.open },
  systemPreferences: { isTrustedAccessibilityClient: mocks.accessibility, getMediaAccessStatus: mocks.screen },
  BrowserWindow: { getAllWindows: () => [] }, Notification: class { static isSupported = () => false },
}))
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => ({
  isOwnFrame: mocks.own, confirmLegacyRoot: () => false, isTrustedRoot: () => true, registerTrustedRoot: vi.fn(),
}))

describe('computer-use registered channel', () => {
  beforeAll(async () => {
    vi.spyOn(fs, 'appendFileSync').mockImplementation(() => {})
    vi.spyOn(ComputerUseHost.prototype, 'available', 'get').mockReturnValue(false)
    // Resolve only the backend origin seam; the registered handler and host
    // dispatch remain real, including route selection and injected headers.
    const realRequest = ComputerUseHost.prototype['request']
    vi.spyOn(ComputerUseHost.prototype as any, 'request').mockImplementation(function (this: any, ...args: any[]) {
      this.deps.baseUrl = () => 'http://localhost:9'
      return realRequest.apply(this, args)
    })
    await import('../main/background')
  })
  beforeEach(() => {
    mocks.request.mockReset().mockImplementation(async (c: any) => ({ data: c.method === 'get'
      ? { enabled: false, mode: 'auto', grants: [] } : { ok: true } }))
    mocks.post.mockReset().mockResolvedValue({ data: { mode: 'auto' } })
    mocks.accessibility.mockClear(); mocks.screen.mockClear(); mocks.open.mockClear()
  })
  afterAll(() => vi.restoreAllMocks())
  const call = (req: unknown) => mocks.handlers.get('computer-use')!({ own: true }, req)

  it('rejects a foreign frame before any backend call', () => {
    expect(() => mocks.handlers.get('computer-use')!({ own: false }, { op: 'enable' })).toThrow(/IPC reddedildi/)
    expect(mocks.request).not.toHaveBeenCalled()
  })

  it('returns status and queries permissions without prompting', async () => {
    const answer: any = await call({ op: 'status' })
    expect(answer).toMatchObject({ available: false, running: false, settings: { enabled: false } })
    expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'get', url: 'http://localhost:9/computer-use/settings' }))
    if (process.platform === 'darwin') {
      expect(mocks.accessibility).toHaveBeenCalledWith(false)
      expect(mocks.screen).toHaveBeenCalledWith('screen')
    }
    const headers = mocks.request.mock.calls[0][0].headers
    expect(Object.keys(headers)).toEqual(['X-Session-Token'])
    expect(JSON.stringify(answer)).not.toContain(headers['X-Session-Token'])
  })

  it.each(['enable', 'disable'])('posts %s with both main-owned secrets then reconciles', async op => {
    await call({ op, uiSecret: 'forged', appToken: 'forged' })
    expect(mocks.request.mock.calls[0][0]).toMatchObject({ method: 'post',
      url: 'http://localhost:9/computer-use/settings', data: { enabled: op === 'enable' } })
    const headers = mocks.request.mock.calls[0][0].headers
    expect(headers['X-Session-Token']).toBeTypeOf('string')
    expect(headers['X-Gamachine-UI-Secret']).toBeTypeOf('string')
    expect(headers['X-Gamachine-UI-Secret']).not.toBe('forged')
    expect(mocks.request.mock.calls.some(([c]) => c.method === 'get')).toBe(true)
  })

  it('prompts Accessibility and opens only the fixed screen settings URL', async () => {
    await call({ op: 'request-accessibility' })
    await call({ op: 'open-screen-settings', url: 'forged' })
    if (process.platform === 'darwin') {
      expect(mocks.accessibility).toHaveBeenCalledWith(true)
      expect(mocks.open).toHaveBeenCalledExactlyOnceWith('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture')
    }
    expect(mocks.request).not.toHaveBeenCalled()
  })

  it('restart stops then reconciles', async () => {
    await call({ op: 'restart' })
    expect(mocks.request.mock.calls.map(([c]) => [c.method, c.url])).toEqual([
      ['post', 'http://localhost:9/computer-use/runtime'], ['get', 'http://localhost:9/computer-use/settings'],
      ['post', 'http://localhost:9/computer-use/runtime'],
    ])
  })

  it.each([
    [{ op: 'remove-grant', bundleId: 'com.apple.TextEdit' }, 'delete', '/computer-use/grants/com.apple.TextEdit', undefined],
    [{ op: 'answer-card', gateId: 'cu-123', approved: true, remember: true }, 'post', '/mcp-approval-respond/cu-123', { approved: true, remember: true }],
    [{ op: 'answer-card', gateId: 'cu-123', approved: false, remember: false }, 'post', '/mcp-approval-respond/cu-123', { approved: false, remember: false }],
    [{ op: 'halt', conversationId: 42 }, 'post', '/computer-use/halt/42', undefined],
  ])('dispatches the fixed backend route for %j', async (req, method, route, data) => {
    expect(await call(req)).toEqual({ ok: true })
    expect(mocks.request).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ method,
      url: `http://localhost:9${route}`, data, headers: {
        'X-Session-Token': expect.any(String), 'X-Gamachine-UI-Secret': expect.any(String),
      } }))
  })

  it.each([null, [], {}, { op: 'wat' }, { op: 'remove-grant', bundleId: '../bad' },
    { op: 'answer-card', gateId: 'g', approved: 'true', remember: true },
    { op: 'answer-card', gateId: 'g', approved: true },
    { op: 'answer-card', gateId: '../g', approved: true, remember: true },
    { op: 'halt' }, { op: 'halt', conversationId: 0 }, { op: 'halt', conversationId: -1 },
    { op: 'halt', conversationId: 1.5 }, { op: 'halt', conversationId: '7' },
    { op: 'halt', conversationId: Number.MAX_SAFE_INTEGER + 1 }, { op: 'halt', conversationId: NaN },
  ])('rejects malformed arguments %j', async req => {
    expect(await call(req)).toHaveProperty('error')
    expect(mocks.request).not.toHaveBeenCalled()
  })

  it('does not leak request headers through HTTP errors', async () => {
    mocks.request.mockRejectedValue({ config: { headers: { 'X-Gamachine-UI-Secret': 'sensitive' } } })
    const result = await call({ op: 'status' })
    expect(result).toHaveProperty('error')
    expect(JSON.stringify(result)).not.toContain('sensitive')
  })

  it('approval-mode-set reconciles after a successful write', async () => {
    const reconcile = vi.spyOn(ComputerUseHost.prototype, 'reconcile').mockResolvedValue()
    try {
      // This existing handler resolves its origin directly. With no backend
      // port it refuses before posting; its successful path is also checked
      // below in source to ensure reconcile follows the awaited POST.
      const source = fs.readFileSync(path.join(process.cwd(), 'main/background.ts'), 'utf8')
      const handler = source.slice(source.indexOf("handleSecure('approval-mode-set'"), source.indexOf('// Maker profile reset'))
      expect(handler.indexOf('await computerUse.reconcile()')).toBeGreaterThan(handler.indexOf('await axios.post('))
      expect(handler.indexOf('await computerUse.reconcile()')).toBeLessThan(handler.indexOf('return response.data'))
      const writeCatch = handler.indexOf('} catch (error)')
      const writeFailure = handler.indexOf("throw new Error(text || 'Çalışma modu backend’e iletilemedi.')")
      const reconcileCall = handler.indexOf('await computerUse.reconcile()')
      expect(writeCatch).toBeGreaterThan(handler.indexOf('await axios.post('))
      expect(writeFailure).toBeGreaterThan(writeCatch)
      expect(handler.slice(writeFailure, reconcileCall)).toMatch(/\n  }\n  try {\s*$/)
      expect(handler.slice(reconcileCall)).toContain("console.error('[cua] reconcile after mode change failed', (error as Error)?.message)")
    } finally { reconcile.mockRestore() }
  })
})
