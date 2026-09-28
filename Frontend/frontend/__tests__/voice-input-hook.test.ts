/**
 * Voice dictation recording hook.
 *
 * jsdom has neither `navigator.mediaDevices` nor `AudioContext` (measured: both
 * are `undefined` under vitest's jsdom environment), so the whole capture graph
 * is stubbed here. What is being measured is not Web Audio — it is the four
 * things that can go wrong in a way the user cannot diagnose: which failure
 * gets which named error, that the request carries the session token, that the
 * microphone is released BEFORE the request rather than after, and that a late
 * response cannot write into a composer that is gone.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  return { __esModule: true, default: { post, defaults: { headers: { common: {} } } }, post }
})

import axios from 'axios'
import { useVoiceInput, LIVE_TEXT_DELAY_MS } from '../renderer/hooks/home/useVoiceInput'
import { WAV_MAX_BYTES } from '../renderer/lib/wav'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  defaults: { headers: { common: Record<string, string> } }
}

const API = 'http://127.0.0.1:8000'

let track: { stop: ReturnType<typeof vi.fn> }
let lastNode: any
let lastCtx: any
// `any`: these are reassigned per test and called through a spread, which
// `Mock<Procedure | Constructable>` does not model as callable.
let getUserMedia: any
let addModule: any
let contextRate = 16000
// Per-test overrides for the three graph steps that can fail AFTER the
// microphone has already been acquired - the window where a leak is possible.
let closeImpl: () => any
let createSourceImpl: () => any
let onNodeConstruct: (() => void) | null
let onHandlerAssigned: ((handler: any) => void) | null
// The three routes of the chunk-session contract, stubbed per test. Routing by
// URL rather than by call order: the scheduler decides how many chunk POSTs
// happen between the open and the finish, so an index-based stub would depend
// on timing the test does not control.
let sessionRespond: (body: any) => any
let chunkRespond: (body: any) => any
let finishRespond: (body: any) => any

class FakeAudioContext {
  sampleRate: number
  audioWorklet = { addModule: (...a: any[]) => addModule(...a) }
  close = vi.fn(() => closeImpl())
  createMediaStreamSource = vi.fn(() => createSourceImpl())
  constructor(options?: { sampleRate?: number }) {
    // The browser may refuse the requested rate; the stub honours it so the
    // default path is the no-resample one, and the 48 kHz case is set up per test.
    this.sampleRate = contextRate !== 16000 ? contextRate : (options?.sampleRate ?? contextRate)
    lastCtx = this
  }
}

class FakeAudioWorkletNode {
  port: any
  connect = vi.fn()
  disconnect = vi.fn()
  constructor(_ctx: any, _name: string) {
    onNodeConstruct?.()
    lastNode = this
    // A real MessagePort delivers asynchronously, so a message can never
    // interleave with the code installing the handler. The setter hook lets a
    // test force that interleaving anyway, which is how the ordering invariant
    // is measured rather than assumed.
    let handler: any = null
    this.port = {
      close: vi.fn(),
      get onmessage() { return handler },
      set onmessage(h: any) { handler = h; onHandlerAssigned?.(h) },
    }
  }
}

const failWith = (name: string) => {
  const err: any = new Error(name)
  err.name = name
  return err
}

beforeEach(() => {
  contextRate = 16000
  lastNode = null
  lastCtx = null
  closeImpl = () => Promise.resolve()
  createSourceImpl = () => ({ connect: vi.fn(), disconnect: vi.fn() })
  onNodeConstruct = null
  onHandlerAssigned = null
  track = { stop: vi.fn() }
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [track] })
  addModule = vi.fn().mockResolvedValue(undefined)
  mockedAxios.post.mockReset()
  sessionRespond = () => ({ data: { session_id: 'sess-1', lang: 'tr' } })
  chunkRespond = () => ({ data: { partial: '', bytes: 0 } })
  finishRespond = () => ({ data: { text: '', duration_ms: 0 } })
  mockedAxios.post.mockImplementation((url: string, body: any) => {
    if (url.endsWith('/transcribe/session')) return Promise.resolve(sessionRespond(body))
    if (url.endsWith('/finish')) return Promise.resolve(finishRespond(body))
    return Promise.resolve(chunkRespond(body))
  })
  mockedAxios.defaults.headers.common = {}
  Object.defineProperty(globalThis.navigator, 'mediaDevices', {
    value: { getUserMedia: (...a: any[]) => getUserMedia(...a) },
    configurable: true,
  })
  ;(globalThis as any).AudioContext = FakeAudioContext
  ;(globalThis as any).AudioWorkletNode = FakeAudioWorkletNode
})

afterEach(() => {
  // Unmount every hook this test rendered BEFORE dropping the fake timers.
  // The chunk scheduler is a 500 ms interval owned by the hook: a test that
  // ends mid-recording used to leave it ticking into the next test, where it
  // posted chunks against that test's mock (measured: 18 unrelated failures).
  cleanup()
  vi.useRealTimers()
})

const speak = (samples = 1600) => {
  act(() => { lastNode.port.onmessage({ data: new Float32Array(samples) }) })
}

const setup = (onText = vi.fn(), lang: 'tr' | 'en' = 'tr') =>
  ({ onText, ...renderHook(() => useVoiceInput({ api: API, lang, onText })) })

const calls = () => mockedAxios.post.mock.calls
const chunkCalls = () => calls().filter((c: any[]) => /\/transcribe\/session\/[^/]+$/.test(String(c[0])))
/** Bytes a chunk body actually carries, decoded — the backend caps this at 65_536. */
const chunkBytes = (call: any[]) => atob(call[1].pcm_base64).length
/**
 * Let the microtask queue drain.
 *
 * `stop()` is no longer one request: it waits out the in-flight chunk, ships
 * the tail and only then asks for the final text. A test that resolves the
 * finish response has to let those awaits run first, or it resolves a promise
 * nobody has created yet and hangs (measured: a 5 s timeout).
 */
const flush = async (rounds = 12) => {
  await act(async () => {
    for (let i = 0; i < rounds; i++) await Promise.resolve()
  })
}

describe('useVoiceInput failures at the microphone', () => {
  it('a denied permission is reported as `permission`, not as a generic failure', async () => {
    getUserMedia.mockRejectedValue(failWith('NotAllowedError'))
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('permission')
    expect(result.current.state).toBe('idle')
  })

  it('SecurityError is the same refusal and gets the same name', async () => {
    getUserMedia.mockRejectedValue(failWith('SecurityError'))
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('permission')
  })

  it('NotFoundError is `noDevice` — a different sentence because the fix is different', async () => {
    getUserMedia.mockRejectedValue(failWith('NotFoundError'))
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('noDevice')
  })

  it('OverconstrainedError also means no usable device', async () => {
    getUserMedia.mockRejectedValue(failWith('OverconstrainedError'))
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('noDevice')
  })
})

describe('useVoiceInput happy path', () => {
  it('start → stop opens a session, ships the audio and finishes it, in that order', async () => {
    finishRespond = () => ({ data: { text: '  merhaba dünya  ', duration_ms: 100 } })
    const { result, onText } = setup()

    await act(async () => { await result.current.start() })
    expect(result.current.state).toBe('recording')
    speak()
    await act(async () => { await result.current.stop() })

    const urls = calls().map((c: any[]) => c[0])
    expect(urls[0]).toBe(`${API}/transcribe/session`)
    expect(calls()[0][1]).toEqual({ lang: 'tr' })
    expect(urls[urls.length - 1]).toBe(`${API}/transcribe/session/sess-1/finish`)
    expect(chunkCalls().length).toBeGreaterThan(0)
    expect(onText).toHaveBeenCalledWith('merhaba dünya')
    expect(result.current.state).toBe('idle')
  })

  it('every request of the session carries the gate token, not just the first', async () => {
    mockedAxios.defaults.headers.common['X-Session-Token'] = 'tok-123'
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    expect(calls().length).toBeGreaterThanOrEqual(3)
    for (const [, , config] of calls()) {
      expect(config.headers['X-Session-Token']).toBe('tok-123')
      expect(config.headers['Content-Type']).toBe('application/json')
    }
  })

  it('the audio on the wire is raw PCM — no RIFF header, which the recogniser would read as sound', async () => {
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak(1600)
    await act(async () => { await result.current.stop() })
    const body = chunkCalls()[0][1]
    expect(typeof body.pcm_base64).toBe('string')
    // 'RIFF' base64-encodes to 'UklGR'; the one-shot route sends that, a chunk must not.
    expect(body.pcm_base64.startsWith('UklGR')).toBe(false)
    expect(chunkBytes(chunkCalls()[0])).toBe(3200)  // 1600 samples, 2 bytes each
  })

  it('the chosen speaking language is what opens the session, not the app language', async () => {
    const { result } = setup(vi.fn(), 'en')
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    expect(calls()[0][1]).toEqual({ lang: 'en' })
  })
})

describe('useVoiceInput answers from the backend', () => {
  const run = async (respond: () => any) => {
    finishRespond = respond
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    return { result, onText }
  }

  it('200 with empty text is `empty` — the request worked, the audio did not', async () => {
    const { result, onText } = await run(() => ({ data: { text: '   ' } }))
    expect(result.current.error?.kind).toBe('empty')
    expect(onText).not.toHaveBeenCalled()
  })

  it('503 is `model` — the recognition files are missing, not the server', async () => {
    const { result } = await run(() => {
      throw { response: { status: 503, data: { detail: 'stt_model_missing' } } }
    })
    expect(result.current.error?.kind).toBe('model')
  })

  it('503 stt_engine_failed on finish is `engine` — the whisper.cpp server crashed, not a missing install', async () => {
    const { result } = await run(() => {
      throw { response: { status: 503, data: { detail: 'stt_engine_failed' } } }
    })
    expect(result.current.error?.kind).toBe('engine')
  })

  it('no response at all is `server`', async () => {
    const { result } = await run(() => { throw new Error('Network Error') })
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.error?.detail).toBe('Network Error')
  })

  it('any other status is `server` and keeps the raw detail for the title', async () => {
    const { result } = await run(() => {
      throw { response: { status: 413, data: { detail: 'stt_too_large' } } }
    })
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.error?.detail).toBe('stt_too_large')
  })
})

/**
 * The 500 ms chunk scheduler.
 *
 * This is the part that makes the words appear while the user speaks, and it
 * is also the part that can quietly ruin a dictation: chunks feed ONE ordered
 * recogniser, so anything that reorders, duplicates or drops one corrupts every
 * partial after it. Fake timers throughout — the interval is the subject.
 */
describe('useVoiceInput chunk scheduler', () => {
  it('ships whatever has accumulated every 500 ms', async () => {
    vi.useFakeTimers()
    const { result } = setup()
    await act(async () => { await result.current.start() })

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkCalls()).toHaveLength(1)

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkCalls()).toHaveLength(2)
  })

  it('a tick that lands on an unfinished send waits instead of overlapping it', async () => {
    vi.useFakeTimers()
    let releaseFirst: () => void = () => {}
    let seen = 0
    chunkRespond = () => {
      seen += 1
      if (seen === 1) {
        return new Promise(res => { releaseFirst = () => res({ data: { partial: 'ilk' } }) })
      }
      return { data: { partial: 'ikinci' } }
    }
    const { result } = setup()
    await act(async () => { await result.current.start() })

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    // Four ticks went by and the first answer never came: still one request.
    expect(chunkCalls()).toHaveLength(1)

    speak(8000)
    await act(async () => { releaseFirst(); await vi.advanceTimersByTimeAsync(500) })
    expect(chunkCalls()).toHaveLength(2)
  })

  it('the partial from each answer is what the composer reads, once the live-text delay has passed', async () => {
    vi.useFakeTimers()
    chunkRespond = () => ({ data: { partial: 'merhaba d', bytes: 16000 } })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.partialText).toBe('')

    // Each tick needs its own fresh audio, or the scheduler finds an empty
    // queue and skips the request entirely (no chunk, no new answer).
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })   // 1000 ms — still gated
    expect(result.current.partialText).toBe('')

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })   // 1500 ms
    expect(result.current.partialText).toBe('merhaba d')
  })

  it('no live text appears before LIVE_TEXT_DELAY_MS, however many answers arrive', async () => {
    vi.useFakeTimers()
    chunkRespond = () => ({ data: { partial: 'gecikmeli metin', bytes: 16000 } })
    const { result } = setup()
    await act(async () => { await result.current.start() })

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(result.current.partialText).toBe('')

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })   // 1000 ms
    expect(result.current.partialText).toBe('')

    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(LIVE_TEXT_DELAY_MS - 1000) })  // 1500 ms
    expect(result.current.partialText).toBe('gecikmeli metin')
  })

  it('a failed chunk is retried with the next one, so no spoken audio is dropped', async () => {
    vi.useFakeTimers()
    let failNext = true
    chunkRespond = () => {
      if (failNext) { failNext = false; throw { response: { status: 500, data: { detail: 'boom' } } } }
      return { data: { partial: 'tamam' } }
    }
    const { result } = setup()
    await act(async () => { await result.current.start() })

    speak(4000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkBytes(chunkCalls()[0])).toBe(8000)
    // One dropped request must not cost the user a word they cannot re-speak.
    expect(result.current.state).toBe('recording')

    speak(4000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkBytes(chunkCalls()[1])).toBe(16000)  // the failed 8000 plus the new 8000
    // Still inside the 1.5 s live-text delay (1000 ms elapsed): the successful
    // answer above is not shown yet.
    expect(result.current.partialText).toBe('')

    // Past the delay: the next answer (same text) is what the composer sees.
    speak(4000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(result.current.partialText).toBe('tamam')
  })

  it('three consecutive failures end the recording with a `server` error', async () => {
    vi.useFakeTimers()
    chunkRespond = () => { throw new Error('Network Error') }
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })

    speak(4000)
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })

    expect(chunkCalls()).toHaveLength(3)
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.state).toBe('idle')
    expect(result.current.partialText).toBe('')
    expect(track.stop).toHaveBeenCalled()
    expect(onText).not.toHaveBeenCalled()
    // The abandoned session is handed back rather than left to the 90 s TTL.
    expect(calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))).toHaveLength(1)
  })

  it('stop ships the tail the scheduler never got to, before asking for the final text', async () => {
    const { result, onText } = setup()
    finishRespond = () => ({ data: { text: 'son cumle' } })
    await act(async () => { await result.current.start() })
    speak(1600)
    await act(async () => { await result.current.stop() })

    const urls = calls().map((c: any[]) => String(c[0]))
    expect(chunkBytes(chunkCalls()[0])).toBe(3200)
    expect(urls.lastIndexOf(`${API}/transcribe/session/sess-1`))
      .toBeLessThan(urls.indexOf(`${API}/transcribe/session/sess-1/finish`))
    expect(onText).toHaveBeenCalledWith('son cumle')
  })
})

describe('useVoiceInput opening the session', () => {
  it('503 while opening is `model` — the recognition files are missing', async () => {
    sessionRespond = () => { throw { response: { status: 503, data: { detail: 'stt_model_missing' } } } }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('model')
    expect(result.current.state).toBe('idle')
    // The microphone was already live when the open failed.
    expect(track.stop).toHaveBeenCalled()
  })

  it('503 stt_engine_failed while opening is `engine`, not `model`', async () => {
    sessionRespond = () => { throw { response: { status: 503, data: { detail: 'stt_engine_failed' } } } }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('engine')
    expect(result.current.state).toBe('idle')
    // The microphone was already live when the open failed.
    expect(track.stop).toHaveBeenCalled()
  })

  it('503 stt_busy is `server`, not `model` — every slot is taken, nothing is broken', async () => {
    sessionRespond = () => { throw { response: { status: 503, data: { detail: 'stt_busy' } } } }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.error?.detail).toContain('stt_busy')
  })

  it('an unreachable backend while opening is `server` and leaves nothing recording', async () => {
    sessionRespond = () => { throw new Error('Network Error') }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.state).toBe('idle')
    expect(lastCtx.close).toHaveBeenCalled()
    expect(chunkCalls()).toHaveLength(0)
  })
})

describe('useVoiceInput resource release', () => {
  it('the microphone is released BEFORE the request resolves, not after', async () => {
    // The OS recording indicator follows the live track. Releasing it after the
    // round trip would leave the light on for the whole transcription, which
    // reads as "still listening".
    let resolveFinish: (v: any) => void = () => {}
    finishRespond = () => new Promise(res => { resolveFinish = res })
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak()

    let stopped: Promise<void> = Promise.resolve()
    act(() => { stopped = result.current.stop() as unknown as Promise<void> })

    // Synchronous, before anything is awaited: this is the ordering invariant.
    expect(track.stop).toHaveBeenCalled()
    await flush()
    expect(onText).not.toHaveBeenCalled()
    expect(result.current.state).toBe('transcribing')

    await act(async () => { resolveFinish({ data: { text: 'ok' } }); await stopped })
    expect(onText).toHaveBeenCalledWith('ok')
  })

  it('cancel hands the session back with discard and never asks for a transcript', async () => {
    // The session holds a recogniser on the server and MAX_SESSIONS is 4, so a
    // cancel that just walked away would burn a slot until the 90 s idle TTL.
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { result.current.cancel() })
    expect(track.stop).toHaveBeenCalled()
    expect(result.current.state).toBe('idle')
    const finishes = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishes).toHaveLength(1)
    expect(finishes[0][1]).toEqual({ discard: true })
    expect(onText).not.toHaveBeenCalled()
  })

  it('the live partial is cleared once the recording is over, so nothing stale is left behind', async () => {
    chunkRespond = () => ({ data: { partial: 'yarim cumle', bytes: 3200 } })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    expect(result.current.partialText).toBe('')
  })

  it('unmounting mid-recording stops the tracks and no late answer reaches onText', async () => {
    let resolveFinish: (v: any) => void = () => {}
    finishRespond = () => new Promise(res => { resolveFinish = res })
    const { result, onText, unmount } = setup()
    await act(async () => { await result.current.start() })
    speak()
    act(() => { void result.current.stop() })
    await flush()
    unmount()
    expect(track.stop).toHaveBeenCalled()
    await act(async () => { resolveFinish({ data: { text: 'too late' } }) })
    await flush()
    expect(onText).not.toHaveBeenCalled()
  })

  it('unmounting while still recording stops the tracks and discards the session', async () => {
    const { result, unmount } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { unmount() })
    expect(track.stop).toHaveBeenCalled()
    const finishes = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishes).toHaveLength(1)
    expect(finishes[0][1]).toEqual({ discard: true })
  })
})

describe('useVoiceInput limits', () => {
  it('a recording left running stops itself at 60 s and transcribes what it has', async () => {
    vi.useFakeTimers()
    finishRespond = () => ({ data: { text: 'uzun kayıt' } })
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak()
    expect(result.current.state).toBe('recording')

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })

    expect(calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))).toHaveLength(1)
    // No `waitFor` here: fake timers are installed, so its polling would never
    // advance and the test would hang rather than fail (measured).
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(onText).toHaveBeenCalledWith('uzun kayıt')
  })

  it('a 48 kHz device is decimated, so the wire format stays 16 kHz mono', async () => {
    contextRate = 48000
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak(48000)  // one second at the device rate
    await act(async () => { await result.current.stop() })

    // One second of 16 kHz 16-bit mono is 32000 bytes, however many chunks it
    // took to get there.
    const total = chunkCalls().reduce((sum: number, c: any[]) => sum + chunkBytes(c), 0)
    expect(total).toBe(32000)
  })
})

/**
 * Everything below promotes an audit finding into a permanent gate.
 *
 * The shared shape of all six: the microphone is an OS-VISIBLE resource, and
 * each of these paths left the track live with no reference left to stop it.
 * The user's only signal would be a recording indicator that never goes out,
 * which reads as the app listening in the background — the worst thing a
 * dictation feature can do quietly.
 */
describe('useVoiceInput partial teardown when the graph fails after acquisition', () => {
  it('AudioWorkletNode constructor throws → track stopped, context closed, error noDevice', async () => {
    onNodeConstruct = () => { throw new Error('node boom') }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(track.stop).toHaveBeenCalled()
    expect(lastCtx.close).toHaveBeenCalled()
    expect(result.current.error?.kind).toBe('noDevice')
    expect(result.current.state).toBe('idle')
  })

  it('createMediaStreamSource throws → track stopped, context closed, error noDevice', async () => {
    createSourceImpl = () => { throw new Error('source boom') }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(track.stop).toHaveBeenCalled()
    expect(lastCtx.close).toHaveBeenCalled()
    expect(result.current.error?.kind).toBe('noDevice')
  })

  it('source.connect throws → track stopped, context closed, error noDevice', async () => {
    createSourceImpl = () => ({
      connect: vi.fn(() => { throw new Error('connect boom') }),
      disconnect: vi.fn(),
    })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    expect(track.stop).toHaveBeenCalled()
    expect(lastCtx.close).toHaveBeenCalled()
    expect(result.current.error?.kind).toBe('noDevice')
  })
})

describe('useVoiceInput acquisition that finishes after unmount', () => {
  it('unmount while getUserMedia is pending → late stream stopped, no graph installed', async () => {
    let resolveMedia: (v: any) => void = () => {}
    getUserMedia = vi.fn(() => new Promise(res => { resolveMedia = res }))
    const { result, unmount, onText } = setup()
    let pending!: Promise<void>
    act(() => { pending = result.current.start() as unknown as Promise<void> })
    unmount()
    await act(async () => { resolveMedia({ getTracks: () => [track] }); await pending })
    expect(track.stop).toHaveBeenCalled()
    // No context at all: the resumed path has to bail before building anything.
    expect(lastCtx).toBeNull()
    expect(onText).not.toHaveBeenCalled()
    expect(result.current.state).toBe('idle')
  })

  it('unmount while addModule is pending → track stopped and context closed', async () => {
    let resolveModule: () => void = () => {}
    addModule = vi.fn(() => new Promise<void>(res => { resolveModule = res }))
    const { result, unmount } = setup()
    let pending!: Promise<void>
    act(() => { pending = result.current.start() as unknown as Promise<void> })
    // Let `start()` get past getUserMedia and actually park inside the module
    // load — otherwise the earlier post-getUserMedia check would be what stops
    // it and this test would not measure the window it is named for.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    expect(addModule).toHaveBeenCalled()
    expect(lastCtx).not.toBeNull()
    unmount()
    await act(async () => { resolveModule(); await pending })
    expect(track.stop).toHaveBeenCalled()
    expect(lastCtx.close).toHaveBeenCalled()
    expect(lastNode).toBeNull()
  })
})

describe('useVoiceInput cleanup that itself fails', () => {
  it('AudioContext.close rejecting → a rejection handler is attached, so nothing escapes', async () => {
    // Detection follows the audit probe: a thenable that records whether the
    // caller passed an `onRejected`. `void ctx.close()` catches only a
    // SYNCHRONOUS throw, so a rejected close promise escaped as an unhandled
    // rejection while the hook had already nulled its context ref.
    let closeRejectionHandled = false
    closeImpl = () => ({
      then(onFulfilled?: any, onRejected?: any) {
        if (typeof onRejected === 'function') closeRejectionHandled = true
        queueMicrotask(() => {
          if (typeof onRejected === 'function') onRejected(new Error('close refused'))
        })
        return Promise.resolve()
      },
      catch(onRejected?: any) {
        if (typeof onRejected === 'function') closeRejectionHandled = true
        return this.then(undefined, onRejected)
      },
    })
    finishRespond = () => ({ data: { text: 'x' } })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    expect(lastCtx.close).toHaveBeenCalledTimes(1)
    expect(closeRejectionHandled).toBe(true)
  })
})

describe('useVoiceInput duplicate start', () => {
  it('two start() calls before acquisition settles → one stream acquired, cancel stops it', async () => {
    // The old guard read React state, which stays `idle` for the whole async
    // acquisition: both calls passed it, both acquired a stream, and the refs
    // held only the second — so the first track could never be stopped again.
    const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }]
    let call = 0
    getUserMedia = vi.fn(() => {
      const t = tracks[Math.min(call++, tracks.length - 1)]
      return Promise.resolve({ getTracks: () => [t] })
    })
    const { result } = setup()
    let first!: Promise<void>
    let second!: Promise<void>
    act(() => {
      first = result.current.start() as unknown as Promise<void>
      second = result.current.start() as unknown as Promise<void>
    })
    await act(async () => { await Promise.all([first, second]) })
    act(() => { result.current.cancel() })
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(tracks[1].stop).not.toHaveBeenCalled()
  })
})

/**
 * Second audit round. Same class of defect as the first, one layer in: the
 * first round made the post-await checks release what `start()` held, but
 * between two awaits the stream and context live ONLY in `start()`'s locals, so
 * a `cancel()` or `stop()` arriving in that window had nothing to release.
 */
describe('useVoiceInput release during an in-flight acquisition', () => {
  it('partial teardown on async cancel: cancel while addModule is pending releases the held stream and context', async () => {
    let resolveModule: () => void = () => {}
    addModule = vi.fn(() => new Promise<void>(res => { resolveModule = res }))
    const { result } = setup()
    let pending!: Promise<void>
    act(() => { pending = result.current.start() as unknown as Promise<void> })
    await act(async () => {
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
    })
    expect(addModule).toHaveBeenCalledTimes(1)

    // The release must happen NOW, not when the stalled module load finally
    // settles: a worklet fetch that never returns would otherwise leave the
    // microphone indicator lit with no way for the user to turn it off.
    act(() => { result.current.cancel() })
    expect(track.stop).toHaveBeenCalled()
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(lastCtx.close).toHaveBeenCalled()

    await act(async () => { resolveModule(); await pending })
    expect(result.current.state).toBe('idle')
  })

  it('stop during async acquisition: a stop issued while start is pending does not become a recording', async () => {
    let resolveMedia: (v: any) => void = () => {}
    getUserMedia = vi.fn(() => new Promise(res => { resolveMedia = res }))
    const { result } = setup()
    let pending!: Promise<void>
    act(() => { pending = result.current.start() as unknown as Promise<void> })
    expect(result.current.state).toBe('idle')

    act(() => { void result.current.stop() })
    await act(async () => { resolveMedia({ getTracks: () => [track] }); await pending })

    expect(result.current.state).toBe('idle')
    expect(track.stop).toHaveBeenCalled()
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })
})

describe('useVoiceInput state ordering', () => {
  it('premature stop state race: a budget stop during handler install is not overwritten by recording', async () => {
    // The byte-budget callback calls `stop()`. If the handler is installed
    // BEFORE `setState('recording')`, the stop runs first and `start()` then
    // announces `recording` for a graph it has already torn down — an active
    // microphone indicator over a dead capture.
    finishRespond = () => new Promise(() => {})  // never settles
    onHandlerAssigned = (handler) => {
      // One block past the 2 MiB budget (2 bytes per sample), at install time.
      handler({ data: new Float32Array(WAV_MAX_BYTES / 2) })
    }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    await flush()

    expect(track.stop).toHaveBeenCalled()
    expect(calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))).toHaveLength(1)
    expect(result.current.state).not.toBe('recording')
    expect(result.current.state).toBe('transcribing')
  })
})

describe('useVoiceInput cleanup diagnostics', () => {
  it('silent cleanup failure: a rejected AudioContext.close is reported, not swallowed', async () => {
    // Handled is not the same as explained. With an empty catch the hook had
    // neither a diagnostic nor a retained reference, so a context that refused
    // to close left no trace at all for anyone reading a bug report.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    closeImpl = () => Promise.reject(new Error('close refused'))
    finishRespond = () => ({ data: { text: 'x' } })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    expect(lastCtx.close).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls.flat().join(' ')).toContain('close refused')
    warn.mockRestore()
  })
})

/**
 * Promoted from the 9b live-dictation audit (3 Sep 2026, state+resource lens).
 */
describe('useVoiceInput stop() drain and finish failures', () => {
  it('a failed drain chunk is retried, not dropped, before finish is called', async () => {
    // `stop()` used to give up on the FIRST drain failure, throw the still-
    // queued audio away, and finish anyway — delivering a truncated transcript
    // as if the recording had completed cleanly.
    vi.useFakeTimers()
    let failNext = true
    chunkRespond = () => {
      if (failNext) { failNext = false; throw new Error('Network Error') }
      return { data: { partial: 'tamam' } }
    }
    finishRespond = () => ({ data: { text: 'tam cumle' } })
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak(16000)   // queued, not yet sent — no tick has advanced
    await act(async () => { await result.current.stop() })

    expect(chunkCalls()).toHaveLength(2)               // the failure, then the retry
    expect(chunkBytes(chunkCalls()[1])).toBe(chunkBytes(chunkCalls()[0]))  // same audio, not partial
    expect(onText).toHaveBeenCalledWith('tam cumle')
    expect(result.current.error).toBeNull()
  })

  it('three drain failures abandon the recording instead of finishing with a partial result', async () => {
    // The retry above has a ceiling: it is the SAME MAX_CHUNK_FAILURES used
    // during live recording, not an unbounded retry loop.
    vi.useFakeTimers()
    chunkRespond = () => { throw new Error('Network Error') }
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak(16000)
    await act(async () => { await result.current.stop() })

    expect(chunkCalls()).toHaveLength(3)
    expect(result.current.error?.kind).toBe('server')
    expect(result.current.state).toBe('idle')
    expect(onText).not.toHaveBeenCalled()
    // `abandon()` still hands the session back with discard — the count is 1,
    // not 0, and it must not be a normal (non-discard) finish carrying no text.
    const finishCalls = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishCalls).toHaveLength(1)
    expect(finishCalls[0][1]).toEqual({ discard: true })
  })

  it('a normal finish that fails still discards the session instead of leaving it for the TTL', async () => {
    // `sessionIdRef` is cleared for this hook's own bookkeeping before the
    // finish request goes out, so its failure path had nothing left to hand
    // `discardSession`; the slot sat occupied until the 90 s idle TTL even
    // though the user had already seen an error.
    finishRespond = () => { throw new Error('Network Error') }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { await result.current.stop() })

    expect(result.current.error?.kind).toBe('server')
    const finishCalls = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishCalls).toHaveLength(2)
    expect(finishCalls[0][1]).toEqual({})
    expect(finishCalls[1][1]).toEqual({ discard: true })
  })

  it('an old cancelled run settling late cannot clear the new run\'s send guard', async () => {
    // `sendChunk`'s `finally` used to write the shared `sendingRef` back to
    // false unconditionally. A cancelled run's request settling AFTER a new
    // run had already started its own send let the scheduler fire a second,
    // overlapping request for the new run while its first was still pending.
    vi.useFakeTimers()
    const resolvers: Array<(v: any) => void> = []
    chunkRespond = () => new Promise(res => { resolvers.push(res) })
    const { result } = setup()

    await act(async () => { await result.current.start() })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkCalls()).toHaveLength(1)

    act(() => { result.current.cancel() })
    await flush()
    await act(async () => { await result.current.start() })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkCalls()).toHaveLength(2)     // the new run's own first send

    // The old run's send settles now, after the new run's send is in flight.
    resolvers[0]({ data: { partial: 'old' } })
    await flush()
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })

    // A third send here would mean the old run's settle freed the new run's
    // guard while its own second send was still pending.
    expect(chunkCalls()).toHaveLength(2)
    expect(resolvers).toHaveLength(2)
  })
})

/**
 * Promoted from the 9b live-dictation audit (3 Sep 2026, error-path lens).
 */
describe('useVoiceInput session-budget and cleanup-ownership failures', () => {
  it('a session-budget 413 stops cleanly and finishes with what was already accepted', async () => {
    // The route's own contract for this status: the session survives the
    // refusal specifically so the caller can finish the accepted prefix.
    // Retrying it like any other failure used to run the counter to
    // MAX_CHUNK_FAILURES and discard that prefix along with everything else.
    vi.useFakeTimers()
    let chunkNum = 0
    chunkRespond = () => {
      chunkNum += 1
      if (chunkNum === 1) return { data: { partial: 'ok' } }
      throw { response: { status: 413, data: { detail: 'stt_too_large' } } }
    }
    finishRespond = () => ({ data: { text: 'accepted so far' } })
    const { result, onText } = setup()
    await act(async () => { await result.current.start() })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })

    expect(result.current.state).toBe('idle')
    expect(onText).toHaveBeenCalledWith('accepted so far')
    const finishCalls = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishCalls).toHaveLength(1)
    expect(finishCalls[0][1]).toEqual({})
  })

  it('unmounting while the normal finish is still pending still discards the session', async () => {
    // `sessionIdRef` is cleared before the finish request goes out, so
    // `discardSession()` (called from the unmount cleanup) used to find
    // nothing to discard in exactly this window.
    let resolveFinish: (v: any) => void = () => {}
    finishRespond = () => new Promise(res => { resolveFinish = res })
    const { result, unmount } = setup()
    await act(async () => { await result.current.start() })
    speak()
    act(() => { void result.current.stop() })
    await flush()
    unmount()

    const finishCalls = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishCalls.some((c: any[]) => c[1]?.discard === true)).toBe(true)
    resolveFinish({ data: { text: 'too late' } })
    await flush()
  })

  it('cancelling while the normal finish is still pending still discards the session', async () => {
    let resolveFinish: (v: any) => void = () => {}
    finishRespond = () => new Promise(res => { resolveFinish = res })
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    act(() => { void result.current.stop() })
    await flush()
    await act(async () => { result.current.cancel() })

    expect(result.current.state).toBe('idle')
    const finishCalls = calls().filter((c: any[]) => String(c[0]).endsWith('/finish'))
    expect(finishCalls.some((c: any[]) => c[1]?.discard === true)).toBe(true)
    resolveFinish({ data: { text: 'too late' } })
    await flush()
  })

  it('a discard request that cannot reach the backend is logged, not swallowed', async () => {
    // An empty catch on the fire-and-forget discard left repeated abandoned
    // sessions with no trace anywhere that they had failed to clean up.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    finishRespond = () => { throw new Error('discard network down') }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    await act(async () => { result.current.cancel() })
    await flush()

    expect(warn.mock.calls.flat().join(' ')).toContain('session discard failed')
    warn.mockRestore()
  })
})

/**
 * Promoted from the 9b verification round (3 Sep 2026) — findings the FIRST
 * round of fixes did not close, each reproduced against that round's own
 * fixed tree before this round's fix.
 */
describe('useVoiceInput cross-run and graph-build gaps found in the verification round', () => {
  it('an older cancelled run settling its finish cannot clear a newer run\'s pending id', async () => {
    // `pendingFinishIdRef` used to be cleared unconditionally in `finally`,
    // so an OLDER run's finish settling late could wipe the tracking a
    // NEWER run had already installed for its own pending finish.
    let sessionNum = 0
    const finishResolvers: Record<string, (v: any) => void> = {}
    mockedAxios.post.mockImplementation((url: string, body: any) => {
      if (url.endsWith('/transcribe/session')) {
        sessionNum += 1
        return Promise.resolve({ data: { session_id: `sess-${sessionNum}`, lang: 'tr' } })
      }
      if (url.endsWith('/finish')) {
        const id = url.split('/transcribe/session/')[1].split('/')[0]
        if (body?.discard === true) return Promise.resolve({ data: {} })
        return new Promise(res => { finishResolvers[id] = res })
      }
      return Promise.resolve({ data: { partial: '' } })
    })

    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak()
    act(() => { void result.current.stop() })
    await flush()
    expect(finishResolvers['sess-1']).toBeTruthy()

    act(() => { result.current.cancel() })
    await flush()
    await act(async () => { await result.current.start() })
    speak()
    act(() => { void result.current.stop() })
    await flush()
    expect(finishResolvers['sess-2']).toBeTruthy()

    finishResolvers['sess-1']({ data: { text: 'old' } })
    await flush()
    act(() => { result.current.cancel() })
    await flush()

    expect(calls().some((c: any[]) =>
      String(c[0]).endsWith('/sess-2/finish') && c[1]?.discard === true)).toBe(true)
  })

  it('a run that never sends its own chunk does not wait on a stale prior run\'s in-flight promise', async () => {
    // `inFlightRef` used to survive across runs: a run that stops without
    // ever queuing a chunk of its own inherited whatever the PREVIOUS run's
    // last send left there, and `stop()` awaits it before finishing.
    vi.useFakeTimers()
    let chunkNum = 0
    let releaseOld: (v: any) => void = () => {}
    chunkRespond = () => {
      chunkNum += 1
      if (chunkNum === 1) return new Promise(res => { releaseOld = res })
      return { data: { partial: '' } }
    }
    const { result } = setup()
    await act(async () => { await result.current.start() })
    speak(8000)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(chunkNum).toBe(1)

    act(() => { result.current.cancel() })
    await flush()
    await act(async () => { await result.current.start() })

    let secondStop!: Promise<void>
    act(() => { secondStop = result.current.stop() as unknown as Promise<void> })
    await flush()
    // The second run's finish must not be blocked on the first run's promise.
    expect(calls().some((c: any[]) =>
      String(c[0]).endsWith('/finish') && c[1]?.discard !== true)).toBe(true)

    releaseOld({ data: { partial: 'old' } })
    await act(async () => { await secondStop })
  })

  it('a connect() failure during graph build releases the constructed node and source', async () => {
    // The graph-build catch released only `stream`/`ctx`; a constructed node
    // and source that failed to CONNECT had no cleanup call on either.
    const source = { connect: vi.fn(() => { throw new Error('connect refused') }), disconnect: vi.fn() }
    createSourceImpl = () => source
    const { result } = setup()
    await act(async () => { await result.current.start() })

    expect(result.current.error?.kind).toBe('noDevice')
    expect(lastNode.port.close).toHaveBeenCalled()
    expect(lastNode.disconnect).toHaveBeenCalled()
    expect(source.disconnect).toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalled()
  })
})
