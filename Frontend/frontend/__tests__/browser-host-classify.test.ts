import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BrowserHost } from '../main/helpers/browser-host'

// Same jsdom stand-ins as browser-host.test.ts: the isolated-world script runs for real.
beforeEach(() => {
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(() => [{}] as unknown as DOMRectList)
  Element.prototype.scrollIntoView = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'innerText', { configurable: true, get() { return this.textContent } })
})
afterEach(() => {
  vi.restoreAllMocks()
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = ''
})

function fixture(haltFails = false) {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    url: '', title: '', loading: false, destroyed: false,
    isDestroyed: () => wc.destroyed, getURL: () => wc.url, getTitle: () => wc.title, isLoading: () => wc.loading,
    setWindowOpenHandler: vi.fn(), loadURL: vi.fn(async () => {}), stop: vi.fn(), reload: vi.fn(), focus: vi.fn(), close: vi.fn(),
    sendInputEvent: vi.fn(), navigationHistory: {},
    executeJavaScriptInIsolatedWorld: vi.fn(async (_world: number, scripts: { code: string }[]) => (0, eval)(scripts[0].code)),
  })
  const view: any = { webContents: wc, setBounds: vi.fn(), setVisible: vi.fn() }
  const session: any = new EventEmitter()
  Object.assign(session, { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() } })
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const request = vi.fn(async (config: any) => {
    if (haltFails && String(config.url).includes('/computer-use/halt')) throw new Error('backend down')
    return { data: {} }
  })
  const host = new BrowserHost({ window: () => win, createView: () => view, fromPartition: () => session, http: { request } as any,
    baseUrl: () => 'http://x', appToken: 't', uiSecret: 's', lookup: async () => [{ address: '93.184.216.34', family: 4 }] })
  return { host, wc, request }
}
const haltPosts = (request: any) => request.mock.calls.filter((c: any[]) => String(c[0].url).includes('/computer-use/halt'))

async function page(html: string) {
  const f = fixture()
  const reply = f.host.handle({ op: 'navigate', conversation_id: 1, args: { url: 'https://example.com/' } })
  await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalled())
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = html
  f.wc.url = 'https://example.com/'
  f.wc.emit('did-finish-load')
  await reply
  const snap: any = await f.host.handle({ op: 'snapshot', conversation_id: 1, args: {} })
  const describeElement = async (name: string) => {
    const el = snap.elements.find((e: any) => e.name === name)
    expect(el, `element ${name} in snapshot`).toBeTruthy()
    return ((await f.host.handle({ op: 'describe', conversation_id: 1, args: { snapshot_id: snap.snapshot_id, ref: el.ref } })) as any).element
  }
  const describeFocused = async () => (await f.host.handle({ op: 'describe', conversation_id: 1, args: { focused: true } })) as any
  return { ...f, describeElement, describeFocused }
}

describe('submit control classification uses the DOM properties', () => {
  it.each(['foo', ' submit', 'SUBMIT'])('a button with type="%s" is a submit control', async (type) => {
    const { describeElement } = await page(`<form method="post" action="/p"><button type="${type}">Continue</button></form>`)
    const e = await describeElement('Continue')
    expect(e).toMatchObject({ type: 'submit', submits_form: true, form_method: 'post' })
  })

  it('a button with type="button" or "reset" does not submit', async () => {
    const { describeElement } = await page('<form method="post"><button type="button">Plain</button><button type="reset">Clear</button></form>')
    expect((await describeElement('Plain')).submits_form).toBe(false)
    expect((await describeElement('Clear')).submits_form).toBe(false)
  })

  it('an input with an unknown type is a text field and a field next to a reset button does not submit', async () => {
    const { describeElement } = await page('<form method="post"><input type="bogus" aria-label="A"><input aria-label="B"><button type="reset">Clear</button></form>')
    const a = await describeElement('A')
    expect(a.type).toBe('text')
    expect(a.submits_form).toBe(false)
    expect(a.form_submit_name).toBeUndefined()
  })
})

describe('Enter in a field follows the form default button', () => {
  it('reports the default button name even in a GET form', async () => {
    const { describeElement } = await page('<form method="get" action="/s"><input aria-label="Q"><button>Delete account</button></form>')
    const e = await describeElement('Q')
    expect(e.form_submit_name).toBe('Delete account')
    expect(e.form_method).toBe('get')
  })

  it('takes the first submit control in tree order, and a type-less button counts', async () => {
    const { describeElement } = await page('<form><button type="button">Skip</button><input aria-label="Q"><input type="submit" value="First"><button>Later</button></form>')
    expect((await describeElement('Q')).form_submit_name).toBe('First')
  })

  it('uses the default button formmethod and formaction', async () => {
    const { describeElement } = await page('<form method="get" action="/s"><input aria-label="Q"><button formmethod="post" formaction="/pay">Go</button></form>')
    const e = await describeElement('Q')
    expect(e.form_method).toBe('post')
    expect(String(e.form_action)).toMatch(/\/pay$/)
  })

  it('an image input can be the default button', async () => {
    const { describeElement } = await page('<form method="get" action="/s"><input aria-label="Q"><input type="image" alt="Pay" formmethod="post" formaction="/pay"></form>')
    const e = await describeElement('Q')
    expect(e).toMatchObject({ form_method: 'post', form_submit_name: 'Pay' })
    expect(String(e.form_action)).toMatch(/\/pay$/)
  })

  it('a form="id" button outside the form is the default button when it comes first in tree order', async () => {
    const { describeElement } = await page('<button form="f" formmethod="post">Outside</button><form id="f" method="get" action="/s"><input aria-label="Q"><button>Inside</button></form>')
    const e = await describeElement('Q')
    expect(e).toMatchObject({ form_submit_name: 'Outside', form_method: 'post' })
  })

  it('a disabled first submit button: browsers block implicit submission, we stay fail-safe and still describe the form as submitting', async () => {
    const { describeElement } = await page('<form method="post" action="/p"><input aria-label="Q"><button disabled>Off</button><button>On</button></form>')
    expect((await describeElement('Q')).submits_form).toBe(true)
  })

  it('a clicked submitter uses its own formmethod, other fields keep the form method', async () => {
    const { describeElement } = await page('<form method="get" action="/s"><input aria-label="Q"><button formmethod="dialog" formaction="/d">Go</button></form>')
    expect((await describeElement('Go')).form_method).toBe('dialog')
    expect((await describeElement('Go')).form_action).toMatch(/\/d$/)
  })

  it('a form with no default button and one text field still submits and reports no button name', async () => {
    const { describeElement } = await page('<form method="post" action="/p"><input aria-label="Q"></form>')
    const e = await describeElement('Q')
    expect(e.submits_form).toBe(true)
    expect(e.form_submit_name).toBeUndefined()
  })
})

describe('the controls a form owns', () => {
  it('counts a form="id" password field outside the form', async () => {
    const { describeFocused } = await page('<input type="password" aria-label="Secret" form="f"><form id="f" method="get" action="/continue"><button>Continue</button></form>')
    ;(document.querySelector('button') as HTMLButtonElement).focus()
    expect((await describeFocused()).element).toMatchObject({ form_method: 'get', form_has_password: true })
  })

  it('a password field inside the form but owned by another form does not count', async () => {
    const { describeFocused } = await page('<form id="other"></form><form method="get" action="/s"><input type="password" form="other" aria-label="Pw"><button>Go</button></form>')
    ;(document.querySelectorAll('button')[0] as HTMLButtonElement).focus()
    expect((await describeFocused()).element.form_has_password).toBe(false)
  })

  it('a focused field in an open shadow root follows its form default button', async () => {
    const { describeFocused } = await page('<div id="h"></div>')
    const root = document.getElementById('h')!.attachShadow({ mode: 'open' })
    root.innerHTML = '<form method="get" action="/search"><input aria-label="Query"><button formmethod="post" formaction="/commit">Continue</button></form>'
    ;(root.querySelector('input') as HTMLInputElement).focus()
    const e = (await describeFocused()).element
    expect(e).toMatchObject({ submits_form: true, form_method: 'post', form_submit_name: 'Continue' })
    expect(String(e.form_action)).toMatch(/\/commit$/)
  })
})

describe('element ids', () => {
  it('one element keeps its id and an equal-looking element gets another', async () => {
    const { describeFocused } = await page('<button>Buy now</button><button>Buy now</button>')
    const [a, b] = Array.from(document.querySelectorAll('button')) as HTMLButtonElement[]
    a.focus()
    const first = (await describeFocused()).element
    b.focus()
    const second = (await describeFocused()).element
    a.focus()
    const again = (await describeFocused()).element
    expect(first.name).toBe(second.name)
    expect(typeof first.element_id).toBe('string')
    expect(second.element_id).not.toBe(first.element_id)
    expect(again.element_id).toBe(first.element_id)
  })

  it('a new document at the same URL never repeats an id', async () => {
    const ids: string[] = []
    for (let visit = 0; visit < 2; visit++) {
      const { describeFocused } = await page('<button>Buy now</button>')
      ;(document.querySelector('button') as HTMLButtonElement).focus()
      ids.push((await describeFocused()).element.element_id)
    }
    expect(ids[0]).not.toBe(ids[1])
  })
})

describe('form properties shadowed by named controls', () => {
  it('a POST form with name=method/action/elements controls keeps its real method and action', async () => {
    const { describeElement } = await page('<form method="post" action="/real"><input name="method"><input name="action"><input name="elements"><input aria-label="Q"><button>Send</button></form>')
    for (const name of ['Q', 'Send']) {
      const e = await describeElement(name)
      expect(e.form_method).toBe('post')
      expect(String(e.form_action)).toMatch(/\/real$/)
    }
  })

  it('a form with name=querySelector does not throw and still sees its password field', async () => {
    const { describeElement } = await page('<form method="post"><input name="querySelector"><input type="password" aria-label="Pw"><button>Go</button></form>')
    const e = await describeElement('Go')
    expect(e).toMatchObject({ form_method: 'post', submits_form: true, form_has_password: true })
  })
})

describe('describe {focused: true}', () => {
  it('describes the element that has focus', async () => {
    const { describeFocused } = await page('<form method="post" action="/p"><input aria-label="Q"><button>Pay now</button></form>')
    ;(document.querySelector('button') as HTMLButtonElement).focus()
    const reply = await describeFocused()
    expect(reply.element).toMatchObject({ name: 'Pay now', submits_form: true, form_method: 'post' })
  })

  it('descends into an open shadow root', async () => {
    const { describeFocused } = await page('<div id="h"></div>')
    const root = document.getElementById('h')!.attachShadow({ mode: 'open' })
    root.innerHTML = '<button>Inner</button>'
    ;(root.querySelector('button') as HTMLButtonElement).focus()
    expect((await describeFocused()).element.name).toBe('Inner')
  })

  it('reports focus inside a frame', async () => {
    const { describeFocused } = await page('<iframe></iframe>')
    ;(document.querySelector('iframe') as HTMLIFrameElement).focus()
    expect(document.activeElement?.tagName).toBe('IFRAME')
    expect(await describeFocused()).toMatchObject({ ok: false, error: 'focus_in_frame' })
  })

  it('describes the body when nothing is focused', async () => {
    const { describeFocused } = await page('<button>Pay</button>')
    const reply = await describeFocused()
    expect(reply.element).toMatchObject({ tag: 'body', submits_form: false })
  })

  it('still rejects a describe with neither a ref nor focused', async () => {
    const { host } = await page('<button>Pay</button>')
    expect(await host.handle({ op: 'describe', conversation_id: 1, args: {} })).toMatchObject({ ok: false, error: 'bad_args' })
  })
})

describe('every Stop path reaches the backend halt', () => {
  const start = async (f: ReturnType<typeof fixture>) => { await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} }).catch(() => {}) }

  it('host.halt() posts the backend halt once', async () => {
    const f = fixture()
    await start(f)
    f.host.halt()
    await vi.waitFor(() => expect(haltPosts(f.request)).toHaveLength(1))
    expect(haltPosts(f.request)[0][0].headers['X-Gamachine-UI-Secret']).toBe('s')
  })

  it('the panel Stop posts exactly once and reports halted', async () => {
    const f = fixture()
    await start(f)
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: true })
    await new Promise((r) => setTimeout(r, 20))
    expect(haltPosts(f.request)).toHaveLength(1)
  })

  it('the panel Stop reports a failed backend halt', async () => {
    const f = fixture(true)
    await start(f)
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: false, error: 'Stopping the agent browser failed in the backend.' })
  })

  it('a failed halt() is swallowed', async () => {
    const f = fixture(true)
    await start(f)
    expect(() => f.host.halt()).not.toThrow()
    await new Promise((r) => setTimeout(r, 20))
  })

  it('a halt for a chat posts even after its tab was closed', async () => {
    const f = fixture()
    await start(f)
    await f.host.invoke({ op: 'close', conversationId: 4 })
    expect(await f.host.invoke({ op: 'halt', conversationId: 4 })).toEqual({ halted: true })
    expect(haltPosts(f.request).map((c: any[]) => c[0].url)).toEqual([expect.stringContaining('/computer-use/halt/4')])
  })

  it('a halt without an id posts for every chat the agent acted in, closed tabs included', async () => {
    const f = fixture()
    await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} }).catch(() => {})
    await f.host.handle({ op: 'snapshot', conversation_id: 5, args: {} }).catch(() => {})
    await f.host.invoke({ op: 'close', conversationId: 4 })
    f.host.halt()
    await vi.waitFor(() => expect(haltPosts(f.request)).toHaveLength(2))
  })

  it('a command without a conversation id is refused and never routed to the last chat', async () => {
    const f = fixture()
    await start(f)
    expect(await f.host.handle({ op: 'snapshot', args: {} })).toMatchObject({ ok: false, error: 'no_conversation' })
    expect(await f.host.handle({ op: 'halt' })).toMatchObject({ ok: false, error: 'no_conversation' })
  })

  it('with no active conversation there is no post', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: true })
    expect(haltPosts(f.request)).toHaveLength(0)
  })
})
