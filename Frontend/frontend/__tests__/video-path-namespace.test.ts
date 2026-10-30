/**
 * D4-02 (external audit, high): local video attachments were never
 * translated for Docker mode. `open-video-dialog` returns an absolute HOST
 * path, the renderer kept it as `{kind:'path', path}`, and `useChat.ts` put
 * it in the `chat-stream` request `videos` array unchanged — the backend's
 * `video_extract.py` then calls `os.path.isfile()` on that host spelling
 * INSIDE the container, where only the one bind-mounted tree exists. It
 * cannot work in Docker mode.
 *
 * The fix: `useChat.sendMessage` now runs every `{kind:'path', ...}` video
 * through `backendWorkspacePath` before it leaves the process, exactly like
 * every other filesystem path. A `null` translation (outside the mount, or
 * no bridge answer at all) means there is no container name for the file —
 * it is refused and the user is told, rather than sending a path the backend
 * cannot resolve. `{kind:'url', ...}` entries are not filesystem paths and
 * pass through untouched.
 *
 * `backendWorkspacePath` reads `window.ipc` at CALL time (see
 * backend-workspace-path.test.ts), so each test sets it directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  // `sendMessage` opens a conversation via POST before it ever touches
  // `fetch` (`createNewConversation` needs a real id or it bails early with
  // `chat-stream` never called) — every test here needs that id.
  const get = vi.fn().mockResolvedValue({ data: [] })
  const post = vi.fn().mockResolvedValue({ data: { id: 42 } })
  return { __esModule: true, default: { get, post }, get, post }
})

import { useChat } from '../renderer/hooks/home/useChat'

const API = 'http://x'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any

/** fetch yanıtını taklit eder: gövde `chat-stream`'in okuduğu bir stream değil,
 * bu yüzden `body` yok — sendMessage'in okuma döngüsü reader olmadığı için
 * atlanır ve tek ölçtüğümüz şey GİDEN isteğin gövdesi olur. */
const streamResponse = () => ({ ok: true, status: 200, body: undefined })

const orijinalIpc = (window as any).ipc
afterEach(() => {
  (window as any).ipc = orijinalIpc
  vi.unstubAllGlobals()
})

const kanca = (showToast = vi.fn()) => {
  const { result } = renderHook(() =>
    useChat(API, USER, { provider_type: 'api' } as any, null, showToast, vi.fn(), (n: string) => n),
  )
  return { result, showToast }
}

/** `sendMessage`in yolladığı `chat-stream` gövdesini ayrıştırır. */
const gonderilenGovde = (fetchMock: ReturnType<typeof vi.fn>) => {
  const call = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/chat-stream'))
  expect(call).toBeTruthy()
  return JSON.parse(String(call![1].body))
}

const gonder = async (result: any, videos: any[]) => {
  await act(async () => {
    await result.current.sendMessage(
      'merhaba', '', 'tr', 'auto', 'off',
      vi.fn(), vi.fn(), undefined, false, videos,
    )
  })
}

// A user send also hands the chat's browser tab back (browser-live release); only path translations count here.
const pathCalls = (invoke: { mock: { calls: unknown[][] } }) => invoke.mock.calls.filter(([ch]) => ch !== 'browser-live')

describe('D4-02 — video path namespace', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('mount İÇİNDEKİ video: çevrilmiş yol gönderiliyor', async () => {
    (window as any).ipc = { invoke: async (ch: string, hostPath: string) => {
      if (ch === 'browser-live') return {}
      expect(hostPath).toBe('C:\\Users\\me\\Game\\clip.mp4')
      return '/workspace/clip.mp4'
    } }
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    await gonder(result, [{ kind: 'path', path: 'C:\\Users\\me\\Game\\clip.mp4', name: 'clip.mp4' }])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([{ kind: 'path', path: '/workspace/clip.mp4', name: 'clip.mp4' }])
    expect(showToast).not.toHaveBeenCalled()
  })

  it('mount DIŞINDAKİ video: reddediliyor, kullanıcı bilgilendiriliyor, GÖNDERİLMİYOR', async () => {
    // Bridge var ama mount dışı bir yol için çeviri cevabı yok → null.
    (window as any).ipc = { invoke: async () => '' }
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    await gonder(result, [{ kind: 'path', path: 'D:\\Disaridaki\\clip.mp4', name: 'clip.mp4' }])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([])
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast.mock.calls[0][1]).toBe('warning')
    expect(String(showToast.mock.calls[0][0]).length).toBeGreaterThan(0)
  })

  it('url girdisi DOKUNULMADAN geçiyor — dosya sistemi yolu değil', async () => {
    // Girdi bilerek bir SAPLANTI `path` alanı da taşıyor. Yalnız
    // `body.videos` şekline bakan bir sınama bunu yakalayamaz: `path`
    // değeri `undefined` kalırsa `JSON.stringify` alanı zaten SİLER, yani
    // "'kind'==='path' kontrolü kaldırılsa bile `v.path` tanımsız olduğu için
    // gövde aynı görünür" mutasyonu sessizce yeşil kalırdı (ölçüldü, ilk
    // yazımda tam bu oldu). Burada `path` GERÇEK bir değer taşıdığı için o
    // kontrol kaldırılırsa `backendWorkspacePath` çağrılır ve görünür biçimde
    // BOZAR ('BOZULDU:' öneki) — ayrım artık serileştirmeden bağımsız.
    const invoke = vi.fn(async (_ch: string, hostPath: string) => `BOZULDU:${hostPath}`);
    (window as any).ipc = { invoke }
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    const urlGirdisi = { kind: 'url', url: 'https://example.com/clip.mp4', path: 'C:\\dokunulmamali.mp4' }
    await gonder(result, [urlGirdisi])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([urlGirdisi])
    expect(pathCalls(invoke)).toEqual([])
    expect(showToast).not.toHaveBeenCalled()
  })

  it('karışık liste: içerideki çevrilir, dışarıdaki elenir, url dokunulmaz', async () => {
    (window as any).ipc = { invoke: async (_ch: string, hostPath: string) => {
      if (hostPath === 'C:\\proj\\inside.mp4') return '/workspace/inside.mp4'
      return ''   // dışarıdaki her yol için "cevap yok"
    } }
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    await gonder(result, [
      { kind: 'path', path: 'C:\\proj\\inside.mp4', name: 'inside.mp4' },
      { kind: 'path', path: 'D:\\outside.mp4', name: 'outside.mp4' },
      { kind: 'url', url: 'https://example.com/clip.mp4' },
    ])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([
      { kind: 'path', path: '/workspace/inside.mp4', name: 'inside.mp4' },
      { kind: 'url', url: 'https://example.com/clip.mp4' },
    ])
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast.mock.calls[0][1]).toBe('warning')
    expect(body.videos.find((v: any) => v.kind === 'url')).not.toHaveProperty('path')
  })

  it('Docker KAPALI (kimlik eşleme): normal kullanıcı davranışı DEĞİŞMİYOR', async () => {
    // Köprü var ve girdiyi aynen geri veriyor — `backendWorkspacePath`'in
    // gerçek non-Docker sözleşmesi (identity), bkz. backend-workspace-path.ts.
    (window as any).ipc = { invoke: async (_ch: string, hostPath: string) => hostPath } // identity
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    const hostPath = 'C:\\Users\\me\\Game\\clip.mp4'
    await gonder(result, [{ kind: 'path', path: hostPath, name: 'clip.mp4' }])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([{ kind: 'path', path: hostPath, name: 'clip.mp4' }])
    expect(showToast).not.toHaveBeenCalled()
  })

  it('köprü hiç YOK (no bridge, gerçek non-Docker Electron dışı durum): fail-closed, video reddedilir — sessizce gönderilmez', async () => {
    delete (window as any).ipc
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    await gonder(result, [{ kind: 'path', path: 'C:\\Users\\me\\Game\\clip.mp4', name: 'clip.mp4' }])

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toEqual([])
    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('video listesi yok/boş: gövdede videos alanı sorunsuz kalıyor, çeviri çağrılmıyor', async () => {
    const invoke = vi.fn();
    (window as any).ipc = { invoke }
    const fetchMock = vi.fn().mockResolvedValue(streamResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, showToast } = kanca()

    await gonder(result, undefined as any)

    const body = gonderilenGovde(fetchMock)
    expect(body.videos).toBeUndefined()
    expect(pathCalls(invoke)).toEqual([])
    expect(showToast).not.toHaveBeenCalled()
  })
})
