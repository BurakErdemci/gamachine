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

  it('updates the title and icon of a remembered page only', () => {
    const { history } = make()
    history.update('https://a.example/', { title: 'ignored' })
    expect(history.list()).toEqual([])
    history.visit('https://a.example/')
    history.update('https://a.example/', { title: '  Hello \n world ', favicon: 'https://a.example/f.ico' })
    expect(history.list()[0]).toMatchObject({ title: 'Hello world', favicon: 'https://a.example/f.ico', visits: 1 })
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

  it('clear forgets everything and writes the empty file at once', () => {
    vi.useFakeTimers()
    const { history, state } = make()
    history.visit('https://a.example/')
    history.clear()
    expect(history.list()).toEqual([])
    expect(JSON.parse(state.file!).entries).toEqual([])
    expect(state.writes).toBe(1)
    vi.advanceTimersByTime(5_000)
    expect(state.writes).toBe(1)
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

  it('writes through a temp file and leaves none behind', () => {
    const file = tmp()
    fileIo.write(file, '{"a":1}')
    expect(fileIo.read(file)).toBe('{"a":1}')
    expect(fs.readdirSync(path.dirname(file))).toEqual(['browser-history.json'])
    expect(fileIo.read(file + '.missing')).toBeNull()
  })

  it('retries a rename that Windows refuses with EPERM or EBUSY, then falls back to a direct write', () => {
    const file = tmp()
    const real = fs.renameSync
    let calls = 0
    vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
      if (++calls <= 2) throw Object.assign(new Error('busy'), { code: calls === 1 ? 'EPERM' : 'EBUSY' })
      real(from, to)
    })
    fileIo.write(file, 'retried')
    expect(calls).toBe(3)
    expect(fs.readFileSync(file, 'utf8')).toBe('retried')
    calls = -100
    vi.spyOn(fs, 'renameSync').mockImplementation(() => { throw Object.assign(new Error('locked'), { code: 'EPERM' }) })
    fileIo.write(file, 'direct')
    expect(fs.readFileSync(file, 'utf8')).toBe('direct')
    expect(fs.readdirSync(path.dirname(file))).toEqual(['browser-history.json'])
  })

  it('still lands the data when the rename fails for another reason', () => {
    const file = tmp()
    vi.spyOn(fs, 'renameSync').mockImplementation(() => { throw Object.assign(new Error('no'), { code: 'ENOSPC' }) })
    fileIo.write(file, 'x')
    expect(fs.readFileSync(file, 'utf8')).toBe('x')
  })
})
