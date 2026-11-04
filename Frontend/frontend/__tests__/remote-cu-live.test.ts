// computer-use-live events forwarded to a paired browser (renderer/lib/remote/desktopRunner.ts):
// the PNG frame becomes a small JPEG, frames are throttled, state-only events pass at once.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const ipc = vi.hoisted(() => {
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const stub = {
    invoke: vi.fn(),
    on: vi.fn((channel: string, cb: (...args: unknown[]) => void) => {
      listeners.set(channel, cb)
      return () => { listeners.delete(channel) }
    }),
    listeners,
  }
  ;(globalThis as any).window.ipc = stub
  return stub
})

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get }, post, get }
})

import axios from 'axios'
import { handleRemoteIpcFrame, resetRemoteSubscriptionsForTests } from '../renderer/lib/remote/desktopRunner'

const post = (axios as unknown as { post: ReturnType<typeof vi.fn> }).post
const API = 'http://127.0.0.1:8000'
const PNG = (n: number) => `data:image/png;base64,${btoa('p'.repeat(n))}`

// Each drawn JPEG is `size(side, quality)` bytes of base64; every call is recorded.
let size: (side: number, quality: number) => number
let drawn: Array<{ side: number; quality: number }>

class FakeOffscreenCanvas {
  width: number
  height: number
  constructor(w: number, h: number) { this.width = w; this.height = h }
  getContext() { return { drawImage: () => {} } }
  async convertToBlob({ type, quality }: { type: string; quality: number }) {
    const side = Math.max(this.width, this.height)
    drawn.push({ side, quality })
    return new Blob([new Uint8Array(size(side, quality))], { type })
  }
}

const sentArgs = () => post.mock.calls.map(c => (c[1] as any).args[0] as { frame: string | null; seq?: number })
const flushAsync = async () => { for (let i = 0; i < 20; i++) await vi.advanceTimersByTimeAsync(0) }
const emit = (live: Record<string, unknown>) => ipc.listeners.get('computer-use-live')!(live)

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(1_000_000)
  drawn = []
  size = () => 1000
  post.mockReset()
  post.mockResolvedValue({ data: { subscribed: true } })
  ipc.on.mockClear()
  vi.stubGlobal('OffscreenCanvas', FakeOffscreenCanvas)
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 2560, height: 1440, close: vi.fn() })))
  handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'computer-use-live' }, API, 'tok')
})

afterEach(() => {
  resetRemoteSubscriptionsForTests()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('frame downscale', () => {
  it('turns a large PNG into a JPEG under 400_000 bytes', async () => {
    emit({ active: true, conversationId: 3, frame: PNG(3_000_000) })
    await flushAsync()
    const [sent] = sentArgs()
    expect(sent.frame!.startsWith('data:image/jpeg;base64,')).toBe(true)
    expect(JSON.stringify(post.mock.calls[0][1]).length).toBeLessThan(400_000)
    expect(drawn[0]).toEqual({ side: 1280, quality: 0.75 })
    expect(post.mock.calls[0][1].args[0].conversationId).toBe(3)
  })

  it('steps down in size, then quality, until it fits', async () => {
    // Too big at 1280 and 960; fits only at 720 with the lower quality.
    size = (side, quality) => (side === 720 && quality === 0.6 ? 1000 : 600_000)
    emit({ active: true, frame: PNG(10) })
    await flushAsync()
    expect(drawn).toEqual([
      { side: 1280, quality: 0.75 }, { side: 1280, quality: 0.6 },
      { side: 960, quality: 0.75 }, { side: 960, quality: 0.6 },
      { side: 720, quality: 0.75 }, { side: 720, quality: 0.6 },
    ])
    expect(sentArgs()[0].frame!.startsWith('data:image/jpeg')).toBe(true)
  })

  it('forwards frame null when nothing fits', async () => {
    size = () => 600_000
    emit({ active: true, conversationId: 4, frame: PNG(10) })
    await flushAsync()
    expect(sentArgs()).toHaveLength(1)
    expect(sentArgs()[0].frame).toBeNull()
    expect(post.mock.calls[0][1].args[0].conversationId).toBe(4)
  })

  it('forwards frame null when the image cannot be decoded', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => { throw new Error('bad image') }))
    emit({ active: true, frame: PNG(10) })
    await flushAsync()
    expect(sentArgs()[0].frame).toBeNull()
  })
})

describe('throttle', () => {
  it('forwards at most 2 of 5 frames in 500 ms, the newest last', async () => {
    for (let i = 1; i <= 5; i++) {
      emit({ active: true, seq: i, frame: PNG(10 + i) })
      await vi.advanceTimersByTimeAsync(100)
    }
    await vi.advanceTimersByTimeAsync(600)
    await flushAsync()
    const sent = sentArgs()
    expect(sent.length).toBeLessThanOrEqual(2)
    expect(sent.length).toBeGreaterThanOrEqual(1)
    expect(sent[sent.length - 1].seq).toBe(5)
  })

  it('lets state-only events through at once and drops an older waiting frame', async () => {
    emit({ active: true, seq: 1, frame: PNG(10) })
    await flushAsync()
    emit({ active: true, seq: 2, frame: PNG(10) })
    emit({ active: false, seq: 3, frame: null })
    expect(post).toHaveBeenCalledTimes(2)
    expect(sentArgs()[1]).toMatchObject({ seq: 3, frame: null })
    await vi.advanceTimersByTimeAsync(1000)
    await flushAsync()
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('does not let a frame still encoding overwrite a newer state', async () => {
    emit({ active: true, seq: 1, frame: PNG(10) })
    emit({ active: false, seq: 2, frame: null })
    await flushAsync()
    expect(sentArgs().map(a => a.seq)).toEqual([2])
  })

  it('sends nothing for other channels through the frame path', async () => {
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'open-conversation' }, API, 'tok')
    ipc.listeners.get('open-conversation')!({ frame: PNG(10) })
    expect((post.mock.calls[0][1] as any).args[0].frame).toBe(PNG(10))
  })
})

describe('slow decoder (starvation)', () => {
  const slowDecoder = (ms: number) => vi.stubGlobal('createImageBitmap', vi.fn(
    () => new Promise(resolve => setTimeout(() => resolve({ width: 2560, height: 1440, close: vi.fn() }), ms))))

  it('delivers frames during continuous 125 ms capture with a 200 ms decoder, sends >= 500 ms apart', async () => {
    slowDecoder(200)
    const sentAt: number[] = []
    post.mockImplementation(async () => { sentAt.push(Date.now()); return { data: { subscribed: true } } })
    for (let i = 0; i < 24; i++) {
      emit({ active: true, seq: i, frame: PNG(10) })
      await vi.advanceTimersByTimeAsync(125)
    }
    expect(sentAt.length).toBeGreaterThanOrEqual(3)
    for (let i = 1; i < sentAt.length; i++) expect(sentAt[i] - sentAt[i - 1]).toBeGreaterThanOrEqual(500)
    expect(sentArgs().every(a => a.frame!.startsWith('data:image/jpeg'))).toBe(true)
  })

  it('leaves at most one unresolved decode while the decoder stalls (9 frames, 500 ms apart, inside the 5 s encode timeout)', async () => {
    const decode = vi.fn(() => new Promise(() => {}))
    vi.stubGlobal('createImageBitmap', decode)
    for (let i = 0; i < 9; i++) {
      emit({ active: true, seq: i, frame: PNG(10) })
      await vi.advanceTimersByTimeAsync(500)
    }
    expect(decode.mock.calls.length).toBeLessThanOrEqual(1)
  })

  it('unsubscribe drops the pending frame and the in-flight result', async () => {
    slowDecoder(200)
    emit({ active: true, seq: 1, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(50)
    emit({ active: true, seq: 2, frame: PNG(10) })
    resetRemoteSubscriptionsForTests()
    await vi.advanceTimersByTimeAsync(2000)
    await flushAsync()
    expect(post).not.toHaveBeenCalled()
  })

  it('a state-only event does not cancel the encode in flight', async () => {
    slowDecoder(200)
    emit({ active: true, seq: 1, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(50)
    emit({ active: true, seq: 2, frame: null })
    await vi.advanceTimersByTimeAsync(400)
    // The finished encode carries the newest event's state (seq 2), only its frame is the encoded one.
    expect(sentArgs().map(a => [a.seq, a.frame === null])).toEqual([[2, true], [2, false]])
  })

  it('counts the channel envelope toward the 400_000 byte cap', async () => {
    // 299_940 bytes -> a 399_943 char data URL: under the cap alone, over it inside {channel, args}.
    size = (side, quality) => (side === 1280 && quality === 0.75 ? 299_940 : 1000)
    emit({ active: true, frame: PNG(10) })
    await flushAsync()
    expect(JSON.stringify(post.mock.calls[0][1]).length).toBeLessThanOrEqual(400_000)
    expect(sentArgs()[0].frame!.length).toBeLessThan(399_943)
  })
})

describe('second audit round', () => {
  const slowDecoder = (ms: number) => vi.stubGlobal('createImageBitmap', vi.fn(
    () => new Promise(resolve => setTimeout(() => resolve({ width: 2560, height: 1440, close: vi.fn() }), ms))))

  it('R5: a finished encode keeps the newest state (haltError, conversation), not the older one', async () => {
    slowDecoder(200)
    emit({ active: true, conversationId: 1, haltError: true, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(50)
    emit({ active: true, conversationId: 2, haltError: false, frame: null })
    await vi.advanceTimersByTimeAsync(400)
    const last = post.mock.calls[post.mock.calls.length - 1][1].args[0]
    expect(last.frame.startsWith('data:image/jpeg')).toBe(true)
    expect(last.conversationId).toBe(2)
    expect(last.haltError).toBe(false)
  })

  it('R5: the frame is dropped when the newest event is active:false', async () => {
    slowDecoder(200)
    emit({ active: true, seq: 1, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(50)
    emit({ active: false, seq: 2, frame: null })
    await vi.advanceTimersByTimeAsync(1000)
    expect(sentArgs().map(a => a.seq)).toEqual([2])
  })

  it('R6: a wall-clock step backwards does not freeze the frames', async () => {
    emit({ active: true, seq: 1, frame: PNG(10) })
    await flushAsync()
    expect(post).toHaveBeenCalledTimes(1)
    vi.setSystemTime(1_000_000 - 3_600_000)
    emit({ active: true, seq: 2, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(700)
    await flushAsync()
    expect(sentArgs().map(a => a.seq)).toEqual([1, 2])
  })

  it('R7: an encode that never settles times out at 5 s and the next frame goes', async () => {
    const decode = vi.fn()
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockImplementation(async () => ({ width: 2560, height: 1440, close: vi.fn() }))
    vi.stubGlobal('createImageBitmap', decode)
    emit({ active: true, seq: 1, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(100)
    emit({ active: true, seq: 2, frame: PNG(10) })
    await vi.advanceTimersByTimeAsync(4000)
    expect(post).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1500)
    await flushAsync()
    expect(sentArgs().map(a => a.seq)).toEqual([2])
    expect(sentArgs()[0].frame).not.toBeNull()
  })
})

describe('R10 subscription generations', () => {
  it('a retired subscription late subscribed:false reply does not end the newer one', async () => {
    let answer!: (v: unknown) => void
    post.mockImplementationOnce(() => new Promise(resolve => { answer = resolve }))
    ipc.listeners.get('computer-use-live')!({ active: true, frame: null })
    expect(post).toHaveBeenCalledTimes(1)
    resetRemoteSubscriptionsForTests()
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'computer-use-live' }, API, 'tok')
    answer({ data: { subscribed: false } })
    await flushAsync()
    expect(ipc.listeners.has('computer-use-live')).toBe(true)
    post.mockResolvedValue({ data: { subscribed: true } })
    emit({ active: true, seq: 9, frame: null })
    expect(sentArgs().some(a => a.seq === 9)).toBe(true)
  })
})
