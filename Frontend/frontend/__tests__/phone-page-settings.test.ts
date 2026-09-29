/**
 * The phone page's model and effort controls: the REAL relay/public/app.js and index.html in
 * jsdom. Only the transport is faked (net.js Link replaced by a scripted link; store.js returns a
 * paired device); every other net.js export, crypto.js and app.js are the real files.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const REL = path.resolve(__dirname, '../../../relay/public')

type Handler = (params: any) => any
const h: { handlers: Record<string, Handler>; calls: Array<{ type: string; params: any }>; link: any } =
  { handlers: {}, calls: [], link: null }

vi.mock('../../../relay/public/store.js', () => ({
  get: async () => ({ pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, vapidPub: '', pushDone: true }),
  put: async () => {}, del: async () => {},
}))
vi.mock('../../../relay/public/net.js', async (orig) => {
  const actual: any = await orig()
  class FakeLink {
    ready = false
    onStatus: any; onPush: any
    constructor(o: any) { this.onStatus = o.onStatus; this.onPush = o.onPush; h.link = this }
    start() { queueMicrotask(() => { this.ready = true; this.onStatus('ready') }) }
    stop() {}
    wake() {}
    async request(type: string, params: any) {
      h.calls.push({ type, params })
      const fn = h.handlers[type]
      if (!fn) return { ok: true, result: {} }
      return await fn(params)
    }
  }
  return { ...actual, Link: FakeLink }
})

const ok = (result: any) => ({ ok: true, result })
const bad = (error: string, extra: any = {}) => ({ ok: false, error, ...extra })
const $ = (id: string) => document.getElementById(id) as any
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
const callsOf = (t: string) => h.calls.filter(c => c.type === t)

const LEVELS = ['auto', 'low', 'high']
let pcEffort: any
let chatModel: { provider_type: string; model_name: string }
const makeCatalog = () => ({
  subscription: [
    { id: 'claude-opus-5', name: 'Opus 5 <img src=x onerror="window.__xss=1">', provider: 'subscription' },
    { id: 'gpt-6-luna', name: 'GPT-6 Luna', provider: 'subscription' },
    { id: 'copilot-auto', name: 'Copilot Auto', provider: 'subscription' },
    { id: 'copilot-gpt-5.5', name: 'Copilot GPT-5.5', provider: 'subscription', disabled: true, disabled_reason: 'plan' },
  ],
  cloud: [{ id: 'gpt-5.5', name: 'GPT 5.5', provider: 'openai', available: true },
          { id: 'nokey-1', name: 'No key', provider: 'groq', available: false }],
  local: [],
})
let catalog = makeCatalog()

const boot = async () => {
  const html = readFileSync(path.join(REL, 'index.html'), 'utf8')
  document.body.innerHTML = html.slice(html.indexOf('<body'), html.indexOf('</body>')).replace(/<script[\s\S]*?<\/script>/g, '').replace(/^<body[^>]*>/, '')
  ;(window as any).scrollTo = vi.fn()
  ;(Element.prototype as any).scrollIntoView = vi.fn()
  h.calls = []
  catalog = makeCatalog()
  pcEffort = { level: 'high', levels: LEVELS }
  chatModel = { provider_type: 'subscription', model_name: 'claude-opus-5' }
  h.handlers = {
    get_config: (p: any) => ok(p?.chat_id !== undefined
      ? { approval_mode: 'step', desktop_effort: pcEffort, ...chatModel, family: 'claude', effort_levels: LEVELS }
      : { approval_mode: 'step', desktop_effort: pcEffort }),
    list_models: () => ok(catalog),
    list_chats: () => ok({ chats: [{ chat_id: '7', title: 'Sohbet 7', status: 'idle', model: chatModel.model_name }] }),
    pending_cards: () => ok({ cards: [] }),
    open_chat: () => ok({ messages: [], events: [] }),
    send_message: () => ok({ status: 'accepted' }),
  }
  vi.resetModules()
  await import('../../../relay/public/app.js')
  await vi.waitFor(() => expect($('chats').querySelector('button')).toBeTruthy())
  $('chats').querySelector('button').click()
  await vi.waitFor(() => expect($('model-select').options.length).toBeGreaterThan(0))
  await vi.waitFor(() => expect($('effort-select').value).toBe('high'))
}

beforeEach(() => { (window as any).__xss = undefined })
afterEach(() => { vi.useRealTimers() })

describe('phone page: model and effort selects (real app.js in jsdom)', () => {
  it('shows the desktop values, catalog names as text, no per-message effort, cloud without key hidden', async () => {
    await boot()
    expect($('model-select').value).toBe('subscription|claude-opus-5')
    expect($('model-select').disabled).toBe(false)
    expect($('effort-select').disabled).toBe(false)
    expect([...$('effort-select').options].map((o: any) => o.value)).toEqual(LEVELS)
    expect($('model-select').querySelector('img')).toBeNull()
    expect((window as any).__xss).toBeUndefined()
    expect([...$('model-select').options].some((o: any) => o.textContent.includes('<img'))).toBe(true)
    expect([...$('model-select').options].some((o: any) => o.textContent === 'No key')).toBe(false)
    $('composer-text').value = 'merhaba'
    $('composer').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
    await vi.waitFor(() => expect(callsOf('send_message').length).toBe(1))
    expect(callsOf('send_message')[0].params).toEqual({ chat_id: '7', text: 'merhaba' })
  })

  it('refreshes on chat_model_changed, effort_changed, page visible and link back', async () => {
    await boot()
    const n0 = callsOf('get_config').filter(c => c.params?.chat_id !== undefined).length
    chatModel = { provider_type: 'subscription', model_name: 'gpt-6-luna' }
    h.link.onPush({ type: 'chat_model_changed', chat_id: '7', provider_type: 'subscription', model_name: 'gpt-6-luna' })
    await vi.waitFor(() => expect($('model-select').value).toBe('subscription|gpt-6-luna'))

    h.link.onPush({ type: 'effort_changed', desktop_effort: { level: 'low', levels: LEVELS } })
    expect($('effort-select').value).toBe('low')

    pcEffort = { level: 'auto', levels: LEVELS }
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect($('effort-select').value).toBe('auto'))

    pcEffort = { level: 'high', levels: LEVELS }
    h.link.ready = false; h.link.onStatus('pc_offline', {})
    expect($('model-select').disabled).toBe(true)
    expect($('effort-select').disabled).toBe(true)
    h.link.ready = true; h.link.onStatus('ready')
    await vi.waitFor(() => expect($('effort-select').value).toBe('high'))
    expect($('model-select').disabled).toBe(false)
    expect(callsOf('get_config').filter(c => c.params?.chat_id !== undefined).length).toBeGreaterThan(n0 + 2)
  })

  it('both selects are disabled while a set_model / set_effort call is out', async () => {
    await boot()
    let release: any
    h.handlers.set_model = () => new Promise(r => { release = () => r(ok({ provider_type: 'subscription', model_name: 'gpt-6-luna' })) })
    $('model-select').value = 'subscription|gpt-6-luna'
    $('model-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(callsOf('set_model').length).toBe(1))
    expect($('model-select').disabled).toBe(true)
    expect($('effort-select').disabled).toBe(true)
    release()
    await vi.waitFor(() => expect($('model-select').disabled).toBe(false))

    h.handlers.set_effort = () => new Promise(r => { release = () => r(ok({ status: 'accepted' })) })
    $('effort-select').value = 'low'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(callsOf('set_effort').length).toBe(1))
    expect($('model-select').disabled).toBe(true)
    expect($('effort-select').disabled).toBe(true)
    release()
    await vi.waitFor(() => expect($('effort-select').disabled).toBe(false))
  })

  const MODEL_FAILURES: Array<[string, any]> = [
    ['not_ready', { needs: 'apikey' }], ['not_ready', { needs: 'install' }], ['not_ready', { needs: 'login' }],
    ['not_ready', { needs: 'service' }], ['not_ready', { needs: null }], ['not_ready', {}], ['busy', {}], ['timeout', {}],
    ['disconnected', {}], ['internal', {}], ['too_large', {}], ['unknown_chat', {}], ['bad_chat_id', {}],
    ['unknown_provider', {}], ['bad_model', {}], ['rate_limited', {}], ['weird_code', {}],
    ['unknown_type', {}], ['bad_request', {}], ['plan_locked', {}],
  ]
  for (const [code, extra] of MODEL_FAILURES) {
    it(`set_model failing with ${code} ${JSON.stringify(extra)} reverts the select and words it in Turkish`, async () => {
      await boot()
      h.handlers.set_model = () => bad(code, extra)
      $('model-select').value = 'subscription|gpt-6-luna'
      $('model-select').dispatchEvent(new Event('change'))
      await vi.waitFor(() => expect($('model-note').textContent).not.toBe('Değiştiriliyor…'))
      expect($('model-select').value).toBe('subscription|claude-opus-5')
      expect($('model-select').disabled).toBe(false)
      const note: string = $('model-note').textContent
      expect(note).toMatch(/[ğüşıöçĞÜŞİÖÇ]|Model|değiş/)
      expect(note).not.toBe(code)
    })
  }
  const EFFORT_FAILURES = ['bad_effort', 'not_ready', 'busy', 'timeout', 'disconnected', 'internal', 'too_large', 'rate_limited', 'weird_code',
    'unknown_type', 'bad_request']
  for (const code of EFFORT_FAILURES) {
    it(`set_effort failing with ${code} reverts the select and words it in Turkish`, async () => {
      await boot()
      h.handlers.set_effort = () => bad(code)
      $('effort-select').value = 'low'
      $('effort-select').dispatchEvent(new Event('change'))
      await vi.waitFor(() => expect($('effort-note').textContent).not.toBe('Değiştiriliyor…'))
      expect($('effort-select').value).toBe('high')
      expect($('effort-select').disabled).toBe(false)
      expect($('effort-note').textContent).toMatch(/[ğüşıöçĞÜŞİÖÇ]|Düşünme/)
    })
  }

  it('desktop_not_ready reverts the effort select with a Turkish note', async () => {
    await boot()
    h.handlers.set_effort = () => ok({ status: 'desktop_not_ready' })
    $('effort-select').value = 'low'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('effort-note').textContent).toMatch(/hazır değil/))
    expect($('effort-select').value).toBe('high')
  })

  it('an accepted request the desktop refuses: after the reconcile read the phone says so and shows the real level', async () => {
    await boot()
    h.handlers.set_effort = () => ok({ status: 'accepted' })
    $('effort-select').value = 'low'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('effort-note').textContent).toMatch(/iletildi/))
    expect($('effort-select').value).toBe('low') // optimistic until the PC says otherwise
    await sleep(1800) // RECONCILE_MS = 1500; the renderer refused, so its snapshot is still 'high'
    await vi.waitFor(() => expect($('effort-note').textContent).toMatch(/uygulamadı/))
    expect($('effort-select').value).toBe('high')
  })

  it('an accepted request the desktop applies: effort_changed confirms it', async () => {
    await boot()
    h.handlers.set_effort = () => ok({ status: 'accepted' })
    $('effort-select').value = 'low'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('effort-note').textContent).toMatch(/iletildi/))
    pcEffort = { level: 'low', levels: LEVELS }
    h.link.onPush({ type: 'effort_changed', desktop_effort: pcEffort })
    expect($('effort-note').textContent).toMatch(/Bilgisayarda değişti/)
    await sleep(1800)
    expect($('effort-select').value).toBe('low')
    expect($('effort-note').textContent).toMatch(/Bilgisayarda değişti/)
  })

  it('the effort select says "unknown" and is disabled when the PC has not reported', async () => {
    await boot()
    pcEffort = null
    h.link.onPush({ type: 'effort_changed', desktop_effort: null })
    expect($('effort-select').disabled).toBe(true)
    expect($('effort-note').textContent).toMatch(/bilinmiyor/)
  })
})

describe('phone page: what the desktop says beyond the level', () => {
  it('an old desktop that does not know the request is told to update, not shown a raw code', async () => {
    await boot()
    h.handlers.set_model = () => bad('unknown_type')
    $('model-select').value = 'subscription|gpt-6-luna'
    $('model-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('model-note').textContent).toMatch(/güncelle/))
    h.handlers.set_effort = () => bad('bad_request')
    $('effort-select').value = 'low'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('effort-note').textContent).toBe('Düşünme seviyesi değişmedi. İstek anlaşılamadı.'))
  })

  it('a plan-locked model is listed but disabled, and the pick the desktop refuses says why', async () => {
    await boot()
    const locked = [...$('model-select').options].find((o: any) => o.value === 'subscription|copilot-gpt-5.5')
    expect(locked.disabled).toBe(true)
    expect(locked.textContent).toMatch(/planında kilitli/)
    const open = [...$('model-select').options].find((o: any) => o.value === 'subscription|copilot-auto')
    expect(open.disabled).toBe(false)
    h.handlers.set_model = () => bad('plan_locked')
    $('model-select').value = 'subscription|gpt-6-luna'
    $('model-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('model-note').textContent).toMatch(/Aboneliğin bu modeli desteklemiyor/))
    expect($('model-select').value).toBe('subscription|claude-opus-5')
  })

  it('the effort select offers none when the desktop model does (OpenAI API)', async () => {
    await boot()
    pcEffort = { level: 'none', levels: ['auto', 'none', 'low'] }
    h.link.onPush({ type: 'effort_changed', desktop_effort: pcEffort })
    expect([...$('effort-select').options].map((o: any) => [o.value, o.textContent])).toEqual([
      ['auto', 'Auto'], ['none', 'None'], ['low', 'Düşük']])
    expect($('effort-select').value).toBe('none')
  })

  it('Ultracode on at the desktop: shown as the choice, noted, and picking a level (even the one under it) is sent', async () => {
    await boot()
    expect($('effort-ultracode').hidden).toBe(true)
    h.link.onPush({ type: 'effort_changed', desktop_effort: { level: 'high', levels: LEVELS, ultracode: true } })
    expect($('effort-ultracode').hidden).toBe(false)
    expect($('effort-ultracode').textContent).toMatch(/Ultracode açık/)
    expect($('effort-select').value).toBe('ultracode')
    expect([...$('effort-select').options][0].textContent).toBe('Ultracode')

    h.handlers.set_effort = () => ok({ status: 'accepted' })
    $('effort-select').value = 'high'
    $('effort-select').dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(callsOf('set_effort').length).toBe(1))
    expect(callsOf('set_effort')[0].params).toEqual({ level: 'high' })
    await vi.waitFor(() => expect($('effort-ultracode').hidden).toBe(true)) // shown as asked, until the PC says otherwise

    h.link.onPush({ type: 'effort_changed', desktop_effort: { level: 'high', levels: LEVELS, ultracode: false } })
    expect($('effort-note').textContent).toMatch(/Bilgisayarda değişti/)
    expect($('effort-select').value).toBe('high')
  })

  it('choosing the Ultracode entry itself sends nothing', async () => {
    await boot()
    h.link.onPush({ type: 'effort_changed', desktop_effort: { level: 'high', levels: LEVELS, ultracode: true } })
    $('effort-select').value = 'ultracode'
    $('effort-select').dispatchEvent(new Event('change'))
    await sleep(50)
    expect(callsOf('set_effort').length).toBe(0)
  })

  it('a default changed at the desktop with no chat open makes the open chat re-read its model', async () => {
    await boot()
    const n0 = callsOf('get_config').filter(c => c.params?.chat_id !== undefined).length
    chatModel = { provider_type: 'subscription', model_name: 'gpt-6-luna' }
    h.link.onPush({ type: 'default_model_changed', provider_type: 'subscription', model_name: 'gpt-6-luna' })
    await vi.waitFor(() => expect($('model-select').value).toBe('subscription|gpt-6-luna'))
    expect(callsOf('get_config').filter(c => c.params?.chat_id !== undefined).length).toBeGreaterThan(n0)
  })
})
