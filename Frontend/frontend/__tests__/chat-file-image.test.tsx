/**
 * Images an agent shows in the chat (its `show_image` tool). The message holds
 * `![caption](gamachine-file:<id>)`; the renderer fetches the stored copy
 * through the backend as a blob (the CSP allows `blob:` and nothing remote),
 * shows it inline with its caption, and opens it large on a click. A local path
 * or an http(s) address is never loaded: it is a chip. The backend announces a
 * new image with a `chat_file` frame on `/wake-stream-all`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn()
  return { default: { post: vi.fn(), get, delete: vi.fn(), put: vi.fn() }, get }
})

import axios from 'axios'
import { resetChatImagesForTests } from '../renderer/components/home/ChatFileImage'
import { MarkdownRenderer } from '../renderer/components/home/MarkdownRenderer'
import { useChat } from '../renderer/hooks/home/useChat'
import { chatUrlTransform } from '../renderer/lib/chatLink'

const mockedAxios = axios as unknown as { get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const ID = '0a1b2c3d-1111-2222-3333-444455556666'
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }

let created: string[]
let revoked: string[]
let backend: string

beforeEach(() => {
  resetChatImagesForTests()
  created = []
  revoked = []
  backend = API
  ;(window as any).ipc = { invoke: vi.fn(async () => backend) }
  URL.createObjectURL = vi.fn(() => { const u = `blob:mock-${created.length + 1}`; created.push(u); return u }) as any
  URL.revokeObjectURL = vi.fn((u: string) => { revoked.push(u) }) as any
  mockedAxios.get.mockReset().mockImplementation(async () => ({ data: new Blob(['png'], { type: 'image/png' }) }))
})

afterEach(() => { cleanup(); delete (window as any).ipc })

const show = (content: string) => render(<MarkdownRenderer content={content} />)
const fileMd = (caption = 'ilk ekran', id = ID) => `![${caption}](gamachine-file:${id})`

describe('the stored image', () => {
  it('loads through the backend as a blob and shows with its caption', async () => {
    show(fileMd())
    expect(screen.getByRole('status')).toBeTruthy()
    await flush()
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('blob:mock-1')
    expect(img.getAttribute('alt')).toBe('ilk ekran')
    expect(screen.getByText('ilk ekran').tagName).toBe('FIGCAPTION')
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.get.mock.calls[0][0]).toBe(`${API}/chat-files/${ID}`)
    expect(mockedAxios.get.mock.calls[0][1]).toMatchObject({ responseType: 'blob' })
    // The figure sits beside, not inside, a paragraph.
    expect(document.querySelector('p figure')).toBeNull()
  })

  it('asks for a size that fits one reply frame in the browser remote', async () => {
    backend = 'remote://backend'
    show(fileMd())
    await flush()
    expect(mockedAxios.get.mock.calls[0][0]).toBe(`remote://backend/chat-files/${ID}?max_bytes=480000`)
  })

  it('fetches once per id however many bubbles show it, and keeps the image when they leave', async () => {
    const first = show(fileMd('a') + '\n\n' + fileMd('b'))
    const second = show(fileMd('c'))
    await flush()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(created).toEqual(['blob:mock-1'])
    first.unmount()
    second.unmount()
    expect(revoked).toEqual([])
  })

  it('a remounted bubble shows the image at once, with no empty box and no new fetch (the jumping chat, 4 Nov 2026)', async () => {
    const view = show(fileMd())
    await flush()
    view.unmount()
    show(fileMd())
    expect(screen.queryByRole('status')).toBeNull()
    expect((screen.getByRole('img') as HTMLImageElement).getAttribute('src')).toBe('blob:mock-1')
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('keeps a URL that arrives after its bubble has gone, for the next bubble', async () => {
    const view = show(fileMd())
    view.unmount()
    await flush()
    expect(created.length).toBe(1)
    expect(revoked).toEqual([])
    show(fileMd())
    expect((screen.getByRole('img') as HTMLImageElement).getAttribute('src')).toBe(created[0])
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('keeps the same image element when the message re-renders with new handlers (no remount)', async () => {
    const view = render(<MarkdownRenderer content={fileMd()} onOpenFile={() => {}} />)
    await flush()
    const img = screen.getByRole('img')
    view.rerender(<MarkdownRenderer content={fileMd()} onOpenFile={() => {}} />)
    await flush()
    expect(screen.getByRole('img')).toBe(img)
  })

  it('revokes the oldest unused images once more than 64 are kept', async () => {
    const id = (n: number) => `0a1b2c3d-1111-2222-3333-${String(n).padStart(12, '0')}`
    for (let n = 0; n < 65; n++) {
      const view = show(fileMd(`r${n}`, id(n)))
      await flush()
      view.unmount()
    }
    expect(created.length).toBe(65)
    expect(revoked).toEqual([created[0]])
  })

  it('shows a chip, not a broken icon, when the image is gone or cannot be read', async () => {
    mockedAxios.get.mockRejectedValueOnce({ response: { status: 404 } })
    show(fileMd('kayıp', '0000aaaa-0000-0000-0000-000000000001'))
    await flush()
    expect(screen.getByText('görüntü: kayıp · bulunamadı')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()

    mockedAxios.get.mockRejectedValueOnce(new Error('network'))
    show(fileMd('bozuk', '0000aaaa-0000-0000-0000-000000000002'))
    await flush()
    expect(screen.getByText('görüntü: bozuk · açılamadı')).toBeTruthy()
  })

  it('opens large on a click and closes with Escape, a click outside, or the close button; focus goes back', async () => {
    show(fileMd())
    await flush()
    const opener = screen.getByRole('button', { name: 'görüntü: ilk ekran' })
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(opener)
    const dialog = screen.getByRole('dialog')
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('blob:mock-1')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Kapat' }))

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)

    fireEvent.click(opener)
    fireEvent.click(screen.getByRole('dialog').querySelector('img')!)   // the picture itself stays open
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.click(screen.getByRole('dialog'))                          // the backdrop
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(opener)
    fireEvent.click(screen.getByRole('button', { name: 'Kapat' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('images that are never loaded', () => {
  it('a local path is a chip with the file name and the reason', async () => {
    const win = String.raw`C:\Users\a\shot 1.png`
    show(`![ekran](<${win}>)`)
    await flush()
    expect(screen.getByText('görüntü: shot 1.png · görüntü aracıyla gösterilmedi')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
    expect(mockedAxios.get).not.toHaveBeenCalled()
  })

  it('an http(s) address is a chip with its host, and nothing is requested', async () => {
    show('![x](https://example.com/a/b.png)')
    await flush()
    expect(screen.getByText('görüntü: example.com · görüntü aracıyla gösterilmedi')).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
    expect(mockedAxios.get).not.toHaveBeenCalled()
  })

  it('the url transform keeps a gamachine-file id and nothing else of that scheme', () => {
    expect(chatUrlTransform(`gamachine-file:${ID}`)).toBe(`gamachine-file:${ID}`)
    expect(chatUrlTransform('gamachine-file:short')).toBe('')
    expect(chatUrlTransform('gamachine-file:../../etc/passwd')).toBe('')
    expect(chatUrlTransform('javascript:alert(1)')).toBe('')
  })
})

describe('the chat_file frame', () => {
  const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
  let wake: (frame: object) => void

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (String(url).includes('/wake-stream-all')) {
        let waiter: ((v: any) => void) | null = null
        const frames: Uint8Array[] = []
        wake = (frame: object) => {
          const bytes = enc(frame)
          if (waiter) { const w = waiter; waiter = null; w({ done: false, value: bytes }) } else frames.push(bytes)
        }
        return Promise.resolve({
          ok: true,
          body: {
            getReader: () => ({
              read: () => frames.length
                ? Promise.resolve({ done: false, value: frames.shift() })
                : new Promise(res => { waiter = res }),
            }),
          },
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    }))
    mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
      if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
      if (String(url).endsWith('/conversations/1/messages')) {
        return { data: [{ id: 3, role: 'user', content: 'göster', smells: [], timestamp: '2026-11-04 10:00:00' }] }
      }
      return { data: [] }
    })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  const hook = () => renderHook(() => useChat(
    API, { id: 1, name: 'b', sessionToken: 'tok' } as any,
    { provider_type: 'subscription', model_name: 'claude-opus-5' } as any, '/ws', vi.fn(), vi.fn(), (n: string) => n))
  const frame = (conversationId: number, id = 4) => ({
    type: 'chat_file', conversation_id: conversationId,
    message: { id, role: 'assistant', content: fileMd(), created_at: '2026-11-04 10:00:05' },
  })

  it('appends the message to a loaded chat once, whatever the number of times it arrives', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    await flush()
    expect(result.current.messages.map((m: any) => m.id)).toEqual([3])

    wake(frame(1))
    await flush()
    wake(frame(1))
    await flush()
    expect(result.current.messages.map((m: any) => [m.id, m.role, m.content, m.timestamp])).toEqual([
      [3, 'user', 'göster', '2026-11-04 10:00:00'],
      [4, 'assistant', fileMd(), '2026-11-04 10:00:05'],
    ])
    expect(result.current.loading).toBe(false)
    expect(result.current.queue).toEqual([])
  })

  it('adds the first image to a chat that is loaded and empty', async () => {
    const { result } = hook()
    mockedAxios.get.mockImplementation(async (url: string) =>
      String(url).endsWith('/conversations/2/messages')
        ? { data: [] }
        : { data: { percent: 1, should_compact: false, message_count: 0 } })
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    await flush()
    expect(result.current.messages).toEqual([])
    wake(frame(2, 5))
    await flush()
    wake(frame(2, 5))
    await flush()
    expect(result.current.messages.map((m: any) => [m.id, m.content])).toEqual([[5, fileMd()]])
  })

  it('leaves a chat that is not loaded here to the server read, and ignores malformed frames', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    await flush()
    wake(frame(9))
    wake({ type: 'chat_file', conversation_id: 1, message: { id: 'x', content: 5 } })
    wake({ type: 'chat_file', conversation_id: 'nope' })
    await flush()
    expect(result.current.messages.map((m: any) => m.id)).toEqual([3])
    mockedAxios.get.mockImplementation(async (url: string) =>
      String(url).endsWith('/conversations/9/messages')
        ? { data: [{ id: 7, role: 'user', content: 'x', smells: [], timestamp: '' }, { id: 8, role: 'assistant', content: fileMd(), smells: [], timestamp: '' }] }
        : { data: { percent: 1, should_compact: false, message_count: 0 } })
    await act(async () => { await result.current.selectConversation({ id: 9 } as any) })
    await flush()
    expect(result.current.messages.map((m: any) => m.id)).toEqual([7, 8])
  })
})
