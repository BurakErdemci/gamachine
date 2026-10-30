import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BrowserHistory, HISTORY_CAP, fileIo, historyUrl, type HistoryIo } from '../main/helpers/browser-history'

function memoryIo(initial: string | null = null) {
  const state = { file: initial, writes: 0 }
  const io: HistoryIo = {
    read: () => state.file,
    write: (_file, data) => { state.file = data; state.writes++ },
  }
  return { state, io }
}

function make(options: { cap?: number; initial?: string | null } = {}) {
  const mem = memoryIo(options.initial ?? null)
  const clock = { now: 1_000 }
  const history = new BrowserHistory('history.json', { cap: options.cap, now: () => clock.now, io: mem.io })
  return { history, clock, ...mem }
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('BrowserHistory', () => {
  it('keeps the newest entries first and counts repeat visits', () => {
    const { history, clock } = make()
    history.visit('https://a.example/', 'A')
    clock.now += 10
    history.visit('https://b.example/', 'B')
    clock.now += 10
    history.visit('https://a.example/', '')
    expect(history.list().map(e => [e.url, e.visits, e.title, e.lastVisit])).toEqual([
      ['https://a.example/', 2, 'A', 1_020],
      ['https://b.example/', 1, 'B', 1_010],
    ])
    expect(history.list(1)).toHaveLength(1)
  })

  it('drops the oldest entries past the cap', () => {
    expect(HISTORY_CAP).toBe(500)
    const { history, clock } = make({ cap: 3 })
    for (let i = 1; i <= 5; i++) { clock.now += 1; history.visit(`https://s${i}.example/`) }
    expect(history.list().map(e => e.url)).toEqual(['https://s5.example/', 'https://s4.example/', 'https://s3.example/'])
    clock.now += 1
    history.visit('https://s3.example/')
    expect(history.list()[0]).toMatchObject({ url: 'https://s3.example/', visits: 2 })
    expect(history.list()).toHaveLength(3)
  })

  it('remembers only plain http(s) pages: no hash, no credentials, no token-like query', () => {
    expect(historyUrl('https://a.example/p?q=unity#top')).toBe('https://a.example/p?q=unity')
    for (const bad of ['file:///etc/passwd', 'about:blank', 'javascript:alert(1)', 'chrome://x', 'not a url', '', 5, null,
      'https://u:p@a.example/', 'https://a.example/cb?token=abc', 'https://a.example/cb?x=1&Code=z', 'https://a.example/' + 'x'.repeat(2_000)])
      expect(historyUrl(bad)).toBeNull()
    const { history } = make()
    history.visit('https://a.example/reset?token=1')
    history.visit('about:blank')
    expect(history.list()).toEqual([])
  })

  it('skips any query name that looks like a secret, and keeps ordinary ones', () => {
    for (const name of ['reset_token', 'auth_token', 'session_token', 'sessionToken', 'csrf_token', 'access-token', 'client_secret',
      'api-key', 'apiKey', 'jwt', 'bearer', 'authorization', 'X-Amz-Signature', 'sig', 'sid', 'otp', 'ticket', 'nonce', 'state', 'code', 'oauth_verifier', 'PASSWORD'])
      expect(historyUrl(`https://a.example/p?${name}=PRIVATE`), name).toBeNull()
    for (const name of ['q', 'query', 'page', 'keywords', 'lang', 'design', 'estate', 'v'])
      expect(historyUrl(`https://a.example/p?${name}=1`), name).not.toBeNull()
    expect(historyUrl('https://www.google.com/search?q=unity+shader')).toBe('https://www.google.com/search?q=unity+shader')
  })

  it('keeps a fetched icon (a data: image) and drops a remote address or an oversized one', () => {
    const { history } = make()
    const icon = 'data:image/png;base64,AQID'
    history.visit('https://a.example/', 'A', 'https://a.example/f.ico')
    expect(history.list()[0].favicon).toBe('')
    history.update('https://a.example/', { favicon: icon })
    expect(history.list()[0].favicon).toBe(icon)
    history.update('https://a.example/', { favicon: 'data:image/png;base64,' + 'A'.repeat(60_000) })
    history.update('https://a.example/', { favicon: 'data:text/html;base64,AAAA' })
    expect(history.list()[0].favicon).toBe(icon)
    const saved = make({ initial: JSON.stringify({ version: 1, entries: [
      { url: 'https://a.example/', title: '', favicon: icon, lastVisit: 2, visits: 1 },
      { url: 'https://b.example/', title: '', favicon: 'https://b.example/f.ico', lastVisit: 1, visits: 1 }] }) })
    expect(saved.history.list().map(e => e.favicon)).toEqual([icon, ''])
  })

  it('updates the title and icon of a remembered page only', () => {
    const { history } = make()
    history.update('https://a.example/', { title: 'ignored' })
    expect(history.list()).toEqual([])
    history.visit('https://a.example/')
    history.update('https://a.example/', { title: '  Hello \n world ', favicon: 'data:image/png;base64,AQID' })
    expect(history.list()[0]).toMatchObject({ title: 'Hello world', favicon: 'data:image/png;base64,AQID', visits: 1 })
  })

  it('saves after a delay and on flush, and loads what it saved', () => {
    vi.useFakeTimers()
    const { history, state, io } = make()
    history.visit('https://a.example/', 'A')
    expect(state.writes).toBe(0)
    vi.advanceTimersByTime(2_500)
    expect(state.writes).toBe(1)
    history.visit('https://b.example/', 'B')
    history.flush()
    expect(state.writes).toBe(2)
    history.flush()
    expect(state.writes).toBe(2)
    const again = new BrowserHistory('history.json', { io })
    expect(again.list().map(e => e.url)).toEqual(['https://b.example/', 'https://a.example/'])
  })

  it('ignores a damaged or hostile file and enforces the cap on load', () => {
    expect(make({ initial: '{not json' }).history.list()).toEqual([])
    expect(make({ initial: JSON.stringify({ entries: 'x' }) }).history.list()).toEqual([])
    const entries = [
      { url: 'https://ok.example/', title: 'ok', favicon: '', lastVisit: 5, visits: 2 },
      { url: 'file:///etc/passwd', title: 'bad', favicon: '', lastVisit: 9, visits: 1 },
      { url: 'https://nan.example/', title: 'bad', favicon: '', lastVisit: 'x', visits: 1 },
      { url: 'https://ok.example/', title: 'dupe', favicon: '', lastVisit: 4, visits: 1 },
      { url: 'https://two.example/', title: 't', favicon: '', lastVisit: 7, visits: 1 },
    ]
    const { history } = make({ cap: 2, initial: JSON.stringify({ version: 1, entries }) })
    expect(history.list().map(e => e.url)).toEqual(['https://two.example/', 'https://ok.example/'])
  })

  it('clear forgets everything and writes the empty file at once', async () => {
    vi.useFakeTimers()
    const { history, state } = make()
    history.visit('https://a.example/')
    expect(await history.clear()).toBe(true)
    expect(history.list()).toEqual([])
    expect(JSON.parse(state.file!).entries).toEqual([])
    expect(state.writes).toBe(1)
    vi.advanceTimersByTime(5_000)
    expect(state.writes).toBe(1)
  })

  it('reports a failed write, keeps the data in memory and writes it again on the next flush', async () => {
    const state = { file: null as string | null, fail: true, writes: 0 }
    const io: HistoryIo = {
      read: () => state.file,
      write: (_file, data) => { state.writes++; if (state.fail) throw Object.assign(new Error('locked'), { code: 'EACCES' }); state.file = data },
    }
    const history = new BrowserHistory('history.json', { io, now: () => 5 })
    history.visit('https://a.example/', 'A')
    expect(await history.flush()).toBe(false)
    expect(history.list()).toHaveLength(1)
    state.fail = false
    expect(await history.flush()).toBe(true)
    expect(JSON.parse(state.file!).entries).toHaveLength(1)
    expect(await history.flush()).toBe(true)
    expect(state.writes).toBe(2)
  })

  it('clear returns false when the file could not be erased', async () => {
    const io: HistoryIo = { read: () => null, write: () => false }
    const history = new BrowserHistory('history.json', { io })
    history.visit('https://a.example/')
    expect(await history.clear()).toBe(false)
    expect(history.list()).toEqual([])
  })

  it('never has two writes in flight: a flush during a slow write waits for it', async () => {
    const order: string[] = []
    let release!: () => void
    const io: HistoryIo = {
      read: () => null,
      write: (_file, data) => {
        const n = JSON.parse(data).entries.length
        order.push(`start${n}`)
        return new Promise<boolean>((resolve) => {
          const done = () => { order.push(`end${n}`); resolve(true) }
          if (n === 1) release = done; else done()
        })
      },
    }
    const history = new BrowserHistory('history.json', { io })
    history.visit('https://a.example/')
    const first = history.flush()
    history.visit('https://b.example/')
    const second = history.flush()
    expect(order).toEqual(['start1'])
    release()
    await Promise.all([first, second])
    expect(order).toEqual(['start1', 'end1', 'start2', 'end2'])
  })
})

describe('BrowserHistory sensitive names', () => {
  it('drops camelCase, SAML and AWS style secrets and keeps ordinary queries', () => {
    for (const name of ['oobCode', 'codeVerifier', 'RelayState', 'sessid', 'SAMLResponse', 'SAMLRequest', 'X-Amz-Credential', 'X-Amz-Signature', 'credential', 'code_challenge', 'sid'])
      expect(historyUrl(`https://a.example/p?${name}=v`), name).toBeNull()
    for (const name of ['q', 'keyword', 'keywords', 'inside', 'design', 'decode', 'estate', 'page'])
      expect(historyUrl(`https://a.example/p?${name}=v`), name).not.toBeNull()
  })

  it('rewrites a file that held a URL the filter now refuses', () => {
    vi.useFakeTimers()
    const stored = JSON.stringify({ version: 1, entries: [
      { url: 'https://a.example/p?oobCode=SECRET', title: 'x', favicon: '', lastVisit: 2, visits: 1 },
      { url: 'https://b.example/', title: 'b', favicon: '', lastVisit: 1, visits: 1 },
    ] })
    const { history, state } = make({ initial: stored })
    expect(history.list().map(e => e.url)).toEqual(['https://b.example/'])
    vi.advanceTimersByTime(2_000)
    expect(state.writes).toBe(1)
    expect(state.file).not.toContain('SECRET')
  })

  it('does not write a file it has nothing to remove from', () => {
    vi.useFakeTimers()
    const { state } = make({ initial: JSON.stringify({ version: 1, entries: [{ url: 'https://b.example/', title: 'b', favicon: '', lastVisit: 1, visits: 1 }] }) })
    vi.advanceTimersByTime(5_000)
    expect(state.writes).toBe(0)
  })
})

describe('fileIo (atomic write)', () => {
  const dirs: string[] = []
  const tmp = () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bh-'))
    dirs.push(dir)
    return path.join(dir, 'nested', 'browser-history.json')
  }
  afterEach(() => { for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true }) })

  it('writes through a temp file and leaves none behind', async () => {
    const file = tmp()
    expect(await fileIo.write(file, '{"a":1}')).toBe(true)
    expect(fileIo.read(file)).toBe('{"a":1}')
    expect(fs.readdirSync(path.dirname(file))).toEqual(['browser-history.json'])
    expect(fileIo.read(file + '.missing')).toBeNull()
  })

  it('sweeps temp files an earlier process left and keeps the history and foreign files', () => {
    const file = tmp()
    const dir = path.dirname(file)
    fs.mkdirSync(dir, { recursive: true })
    for (const name of ['browser-history.json', 'browser-history.json.99999.tmp', 'browser-history.json.4242.tmp', 'other.json.1.tmp', 'browser-history.json.bak'])
      fs.writeFileSync(path.join(dir, name), 'x')
    fileIo.sweep!(file)
    expect(fs.readdirSync(dir).sort()).toEqual(['browser-history.json', 'browser-history.json.bak', 'other.json.1.tmp'])
  })

  it('retries a rename that Windows refuses with EPERM or EBUSY after a growing pause', async () => {
    vi.useFakeTimers()
    const file = tmp()
    const real = fs.renameSync
    let calls = 0
    vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
      if (++calls <= 2) throw Object.assign(new Error('busy'), { code: calls === 1 ? 'EPERM' : 'EBUSY' })
      real(from, to)
    })
    const written = fileIo.write(file, 'retried')
    expect(calls).toBe(1)
    await vi.advanceTimersByTimeAsync(50)
    expect(calls).toBe(2)
    await vi.advanceTimersByTimeAsync(99)
    expect(calls).toBe(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(await written).toBe(true)
    expect(calls).toBe(3)
    expect(fs.readFileSync(file, 'utf8')).toBe('retried')
  })

  it('leaves the existing file untouched and no temp file when the rename keeps failing', async () => {
    vi.useFakeTimers()
    const file = tmp()
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, 'old')
    const direct = vi.spyOn(fs, 'writeFileSync')
    vi.spyOn(fs, 'renameSync').mockImplementation(() => { throw Object.assign(new Error('locked'), { code: 'EPERM' }) })
    const written = fileIo.write(file, 'new')
    await vi.advanceTimersByTimeAsync(1_000)
    expect(await written).toBe(false)
    expect(fs.readFileSync(file, 'utf8')).toBe('old')
    expect(fs.readdirSync(path.dirname(file))).toEqual(['browser-history.json'])
    expect(direct.mock.calls.some(([target]) => target === file)).toBe(false)
  })

  it('does not retry a rename that fails for another reason', async () => {
    const file = tmp()
    const rename = vi.spyOn(fs, 'renameSync').mockImplementation(() => { throw Object.assign(new Error('no'), { code: 'ENOSPC' }) })
    expect(await fileIo.write(file, 'x')).toBe(false)
    expect(rename).toHaveBeenCalledTimes(1)
    expect(fs.readdirSync(path.dirname(file))).toEqual([])
  })
})
