/**
 * Late C# diagnostics and the "not in the Unity project" hint.
 *
 * Measured 28 Sep 2026: the backend waits ~1.2 s for OmniSharp; on a cold start
 * the first answer was empty at 1.34 s and the two errors arrived 60 ms later.
 * The editor asked only on keystrokes, so the user saw nothing until typing
 * again. A new script outside every csproj gets syntax errors only, which reads
 * as "no problems" unless something says so.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get }, post, get }
})

import axios from 'axios'
import {
  useLiveDiagnostics, CHANGE_DEBOUNCE_MS, LATE_DIAGNOSTICS_MS,
} from '../renderer/hooks/home/useLiveDiagnostics'
import { CsharpProjectHint } from '../renderer/components/home/CsharpProjectHint'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

const post = axios.post as unknown as ReturnType<typeof vi.fn>
const get = axios.get as unknown as ReturnType<typeof vi.fn>

const WS = 'C:\\Unity Projeler\\ai proje'
const FILE = `${WS}\\Assets\\Scripts\\New.cs`
const REL = 'Assets/Scripts/New.cs'
const READY = { state: 'ready', detail: '' }

type Props = { openedFilePath: string | null; code: string }

function mount(initial: Props) {
  const problems: Record<string, any[]>[] = []
  let store: Record<string, any[]> = {}
  const setProjectProblems = (u: (p: Record<string, any[]>) => Record<string, any[]>) => {
    store = u(store)
    problems.push(store)
  }
  const hook = renderHook((p: Props) => useLiveDiagnostics({
    API: 'http://api', sessionToken: 'tok', workspacePath: WS, setProjectProblems, ...p,
  }), { initialProps: initial })
  return { ...hook, problems: () => store }
}

// Lets the awaited axios promise settle inside fake time.
const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

beforeEach(() => {
  vi.useFakeTimers()
  post.mockReset()
  get.mockReset()
  post.mockResolvedValue({ data: { problems: [], status: READY, inProject: false } })
  get.mockResolvedValue({ data: { problems: [], status: READY, inProject: false } })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('late diagnostics re-ask', () => {
  it('asks again at each late delay after the change answer, for this file', async () => {
    mount({ openedFilePath: FILE, code: 'class New {}' })
    await flush(CHANGE_DEBOUNCE_MS)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][1]).toEqual({ path: REL, text: 'class New {}' })
    expect(get).not.toHaveBeenCalled()

    await flush(LATE_DIAGNOSTICS_MS[0] - 1)
    expect(get).not.toHaveBeenCalled()
    await flush(1)
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0][0]).toBe('http://api/lsp/diagnostics')
    expect(get.mock.calls[0][1].params).toEqual({ path: REL })

    await flush(LATE_DIAGNOSTICS_MS[1] - LATE_DIAGNOSTICS_MS[0])
    expect(get).toHaveBeenCalledTimes(2)
    await flush(20_000)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('shows the errors that arrived after the change answer', async () => {
    post.mockResolvedValue({ data: { problems: [], status: READY, inProject: true } })
    const late = [{ file: REL, line: 5, column: 9, message: 'CS0103', severity: 'error' }]
    get.mockResolvedValue({ data: { problems: late, status: READY, inProject: true } })
    const h = mount({ openedFilePath: FILE, code: 'x' })
    await flush(CHANGE_DEBOUNCE_MS)
    expect(h.problems()[REL]).toEqual([])
    await flush(LATE_DIAGNOSTICS_MS[0])
    expect(h.problems()[REL]).toEqual(late)
  })

  it('a new keystroke cancels the pending re-asks of the old text', async () => {
    const h = mount({ openedFilePath: FILE, code: 'a' })
    await flush(CHANGE_DEBOUNCE_MS)
    h.rerender({ openedFilePath: FILE, code: 'ab' })
    await flush(LATE_DIAGNOSTICS_MS[0])
    // Only the re-ask of the second change can have fired by now.
    expect(get).toHaveBeenCalledTimes(0)
    await flush(CHANGE_DEBOUNCE_MS)
    expect(get).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('other languages make no request at all', async () => {
    mount({ openedFilePath: `${WS}\\Assets\\notes.md`, code: '# x' })
    await flush(10_000)
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await flush(10_000)
    expect(post).not.toHaveBeenCalled()
    expect(get).not.toHaveBeenCalled()
  })

  it('returning to the window re-asks, so a csproj Unity regenerated is picked up', async () => {
    const h = mount({ openedFilePath: FILE, code: 'x' })
    await flush(CHANGE_DEBOUNCE_MS + 10_000)
    expect(h.result.current.inProject).toBe(false)
    get.mockClear()
    get.mockResolvedValue({ data: { problems: [], status: READY, inProject: true } })
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await flush(0)
    expect(get).toHaveBeenCalledTimes(1)
    expect(h.result.current.inProject).toBe(true)
    await flush(LATE_DIAGNOSTICS_MS[1])
    expect(get).toHaveBeenCalledTimes(1 + LATE_DIAGNOSTICS_MS.length)
  })
})

describe('focus burst coalescing', () => {
  it('coalesces a burst of focus events into at most one bounded refresh cycle', async () => {
    mount({ openedFilePath: FILE, code: 'x' })
    // Let the mount's own change-then-late-diagnostics cycle finish first, so
    // only the focus burst's own requests are being counted below.
    await flush(CHANGE_DEBOUNCE_MS + LATE_DIAGNOSTICS_MS[1] + 100)
    get.mockClear()

    await act(async () => {
      for (let i = 0; i < 10; i++) window.dispatchEvent(new Event('focus'))
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(get.mock.calls.length).toBeLessThanOrEqual(3)
  })

  it('cancels the pending focus cycle on unmount', async () => {
    const h = mount({ openedFilePath: FILE, code: 'x' })
    await flush(CHANGE_DEBOUNCE_MS)
    get.mockClear()
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    h.unmount()
    await flush(10_000)
    expect(get).not.toHaveBeenCalled()
  })
})

describe('not-in-project verdict', () => {
  it('follows the backend and resets when another file opens', async () => {
    const h = mount({ openedFilePath: FILE, code: 'x' })
    expect(h.result.current.inProject).toBe(null)
    await flush(CHANGE_DEBOUNCE_MS)
    expect(h.result.current.inProject).toBe(false)
    h.rerender({ openedFilePath: `${WS}\\Assets\\Scripts\\Player.cs`, code: 'y' })
    expect(h.result.current.inProject).toBe(null)
  })

  it('an unknown verdict (OmniSharp down) is not a "false"', async () => {
    post.mockResolvedValue({ data: { problems: [], status: { state: 'error', detail: 'x' }, inProject: null } })
    const h = mount({ openedFilePath: FILE, code: 'x' })
    await flush(CHANGE_DEBOUNCE_MS)
    expect(h.result.current.inProject).toBe(null)
  })
})

describe('CsharpProjectHint', () => {
  it('tells the user only syntax is checked while the file is outside the project', () => {
    render(<CsharpProjectHint inProject={false} />)
    expect(screen.getByRole('status').textContent).toContain('yalnız yazım hataları denetleniyor')
  })

  it('speaks English in English mode', () => {
    render(
      <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (k, v) => ceviriUygula('en', k, v) }}>
        <CsharpProjectHint inProject={false} />
      </LangContext.Provider>,
    )
    expect(screen.getByRole('status').textContent).toContain('only syntax errors are checked')
  })

  it('disappears once the file is in the project, and when nothing is known', () => {
    const { rerender } = render(<CsharpProjectHint inProject={true} />)
    expect(screen.queryByRole('status')).toBeNull()
    rerender(<CsharpProjectHint inProject={null} />)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('is wired into the editor pane from the live diagnostics verdict', () => {
    const src = readFileSync(join(__dirname, '..', 'renderer', 'pages', 'home.tsx'), 'utf-8')
    expect(src).toMatch(/inProject:\s*csInProject\s*}\s*=\s*useLiveDiagnostics\(/)
    expect(src).toMatch(/<CsharpProjectHint inProject=\{[^}]*csInProject\}/)
  })
})
