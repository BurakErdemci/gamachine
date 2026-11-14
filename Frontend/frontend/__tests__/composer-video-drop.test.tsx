import React, { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AnimatedChatInput, ComposerDraftContext, type ComposerDraft, type ComposerPickers } from '../renderer/components/ui/animated-ai-chat'
import { LangContext, ceviriUygula, tr, en, type Lang } from '../renderer/lib/i18n'

afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

function Host({ onSendMessage, pickersRef, chatId, queue }: {
  onSendMessage: (...args: any[]) => void; pickersRef?: React.Ref<ComposerPickers>; chatId?: number | null; queue?: any
}) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={onSendMessage} isLoading={false}
    pickersRef={pickersRef} currentChatId={chatId} queue={queue} />
}

function dropFiles(box: HTMLElement, files: File[]) {
  fireEvent.drop(box, { dataTransfer: { getData: () => '', files } })
}
function dropFile(box: HTMLElement, file: File) {
  dropFiles(box, [file])
}

function setup(pathForFile: (f: File) => string, invoke?: (ch: string) => Promise<any>) {
  ;(window as any).ipc = { pathForFile, invoke }
  const onSendMessage = vi.fn()
  const pickers = React.createRef<ComposerPickers>()
  const { container } = render(<Host onSendMessage={onSendMessage} pickersRef={pickers} />)
  const box = screen.getByRole('textbox') as HTMLTextAreaElement
  return { onSendMessage, container, box, pickers }
}

const clips = (n: number, ext = 'mp4') => Array.from({ length: n }, (_, i) => new File(['x'], `c${i}.${ext}`, { type: 'video/mp4' }))
const sentVideos = (onSendMessage: ReturnType<typeof vi.fn>) => onSendMessage.mock.calls[0][2]

describe('dropping files on the composer', () => {
  it('sends a dropped video as a path video attachment', () => {
    const { onSendMessage, container, box } = setup(() => 'C:\\v\\clip.mp4')
    fireEvent.change(box, { target: { value: 'look' } })
    dropFile(box, new File(['x'], 'clip.mp4', { type: 'video/mp4' }))
    expect(container.querySelectorAll('.composer-att')).toHaveLength(1)
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledTimes(1)
    expect(onSendMessage.mock.calls[0][2]).toEqual([{ kind: 'path', path: 'C:\\v\\clip.mp4', name: 'clip.mp4' }])
  })

  it('keeps a dropped .txt as a plain file attachment', () => {
    const pathForFile = vi.fn(() => 'C:\\v\\a.txt')
    const { onSendMessage, container, box } = setup(pathForFile)
    fireEvent.change(box, { target: { value: 'look' } })
    dropFile(box, new File(['x'], 'a.txt', { type: 'text/plain' }))
    expect(container.querySelectorAll('.composer-att')).toHaveLength(1)
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage.mock.calls[0][2]).toEqual([])
  })

  it('falls back to the old file behaviour when no path resolves', () => {
    const { onSendMessage, container, box } = setup(() => '')
    fireEvent.change(box, { target: { value: 'look' } })
    dropFile(box, new File(['x'], 'clip.mp4', { type: 'video/mp4' }))
    expect(container.querySelectorAll('.composer-att')).toHaveLength(1)
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage.mock.calls[0][2]).toEqual([])
  })

  it('recognises videos by extension when the browser gives no MIME type', () => {
    for (const name of ['clip.MOV', 'a.webm', 'b.m4v']) {
      cleanup()
      const { onSendMessage, box } = setup(f => 'C:/v/' + f.name)
      fireEvent.change(box, { target: { value: 'look' } })
      dropFile(box, new File(['x'], name, { type: '' }))
      fireEvent.keyDown(box, { key: 'Enter' })
      expect(sentVideos(onSendMessage)).toEqual([{ kind: 'path', path: 'C:/v/' + name, name }])
    }
  })

  it('recognises a video by MIME type when the extension is unknown', () => {
    const { onSendMessage, box } = setup(() => 'C:/v/clip.bin')
    fireEvent.change(box, { target: { value: 'look' } })
    dropFile(box, new File(['x'], 'clip.bin', { type: 'video/mp4' }))
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(sentVideos(onSendMessage)).toEqual([{ kind: 'path', path: 'C:/v/clip.bin', name: 'clip.bin' }])
  })

  it('keeps 5 of 6 dropped videos and says so', () => {
    const { onSendMessage, container, box } = setup(f => 'C:/v/' + f.name)
    fireEvent.change(box, { target: { value: 'look' } })
    expect(container.querySelector('[data-video-cap-note]')).toBeNull()
    dropFiles(box, clips(6))
    expect(container.querySelectorAll('.composer-att')).toHaveLength(5)
    expect(container.querySelector('[data-video-cap-note]')).not.toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(sentVideos(onSendMessage)).toHaveLength(5)
  })

  it('counts picked videos toward the same limit of 5', async () => {
    const invoke = vi.fn(async () => [1, 2, 3].map(i => ({ path: 'C:/p/p' + i + '.mp4', name: `p${i}.mp4` })))
    const { onSendMessage, container, box, pickers } = setup(f => 'C:/v/' + f.name, invoke)
    fireEvent.change(box, { target: { value: 'look' } })
    await act(async () => { pickers.current!.pickVideo() })
    expect(container.querySelectorAll('.composer-att')).toHaveLength(3)
    expect(container.querySelector('[data-video-cap-note]')).toBeNull()
    dropFiles(box, clips(3))
    expect(container.querySelectorAll('.composer-att')).toHaveLength(5)
    expect(container.querySelector('[data-video-cap-note]')).not.toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(sentVideos(onSendMessage)).toHaveLength(5)
  })
})

const NOTE = '[data-video-cap-note]'
const vids = (n: number, tag = 'd') => Array.from({ length: n }, (_, i) => ({ name: `${tag}${i}.mp4`, data: '', type: 'video' as const, path: `C:/${tag}/${i}.mp4` }))

describe('the video limit note and the other ways videos get in', () => {
  it('recognises mkv and avi by extension when the type is empty', () => {
    for (const name of ['a.mkv', 'b.AVI']) {
      cleanup()
      const { onSendMessage, box } = setup(f => 'C:/v/' + f.name)
      fireEvent.change(box, { target: { value: 'look' } })
      dropFile(box, new File(['x'], name, { type: '' }))
      fireEvent.keyDown(box, { key: 'Enter' })
      expect(sentVideos(onSendMessage)).toEqual([{ kind: 'path', path: 'C:/v/' + name, name }])
    }
  })

  it('says the limit in Turkish and in English', () => {
    for (const lang of ['tr', 'en'] as Lang[]) {
      cleanup()
      ;(window as any).ipc = { pathForFile: (f: File) => 'C:/v/' + f.name }
      const { container } = render(
        <LangContext.Provider value={{ lang, setLang: () => {}, t: (k, v) => ceviriUygula(lang, k, v) }}>
          <Host onSendMessage={vi.fn()} />
        </LangContext.Provider>)
      dropFiles(screen.getByRole('textbox'), clips(6))
      expect(container.querySelector(NOTE)!.textContent).toBe(lang === 'tr' ? tr['composer.videoLimit'] : en['composer.videoLimit'])
    }
    expect(tr['composer.videoLimit']).toBe('En fazla 5 video eklenebilir; fazlası eklenmedi.')
    expect(en['composer.videoLimit']).toBe('You can attach up to 5 videos; the rest were not added.')
  })

  it('drops the note once a video is removed, and on send', () => {
    const { onSendMessage, container, box } = setup(f => 'C:/v/' + f.name)
    fireEvent.change(box, { target: { value: 'look' } })
    dropFiles(box, clips(6))
    expect(container.querySelector(NOTE)).not.toBeNull()
    fireEvent.click(container.querySelector('.composer-att button')!)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(4)
    expect(container.querySelector(NOTE)).toBeNull()
    dropFiles(box, clips(2))
    expect(container.querySelector(NOTE)).not.toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledTimes(1)
    expect(container.querySelector(NOTE)).toBeNull()
  })

  it('does not stop text from being sent while the note shows', () => {
    const { onSendMessage, container, box } = setup(f => 'C:/v/' + f.name)
    fireEvent.change(box, { target: { value: 'still goes' } })
    dropFiles(box, clips(7))
    expect(container.querySelector(NOTE)).not.toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage.mock.calls[0][0]).toBe('still goes')
  })

  it('cuts videos taken back from the queue to 5 and says so', () => {
    const item = { id: 1, draft: 'queued', videos: [1, 2, 3, 4, 5].map(i => ({ kind: 'path', path: `C:/q/${i}.mp4`, name: `q${i}.mp4` })) }
    const queue = { items: [item], paused: false, onEdit: vi.fn(() => item), onDelete: vi.fn(), onSendNow: vi.fn(), onResume: vi.fn() }
    ;(window as any).ipc = { pathForFile: (f: File) => 'C:/v/' + f.name }
    const onSendMessage = vi.fn()
    const { container } = render(<Host onSendMessage={onSendMessage} queue={queue} />)
    const box = screen.getByRole('textbox')
    dropFiles(box, clips(3))
    expect(container.querySelector(NOTE)).toBeNull()
    fireEvent.click(container.querySelector('[data-queue-edit]')!)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(5)
    expect(container.querySelector(NOTE)).not.toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(sentVideos(onSendMessage)).toHaveLength(5)
  })

  it('cuts an over-limit stored draft to 5 on mount and on a chat switch, and a send carries 5', () => {
    ;(window as any).ipc = { pathForFile: () => '' }
    const drafts = new Map<string, ComposerDraft>([
      ['1', { text: 'one', attachments: [...vids(8, 'a'), { name: 'n.txt', data: '', type: 'file' }] }],
      ['2', { text: 'two', attachments: vids(7, 'b') }],
    ])
    const onSendMessage = vi.fn()
    const App = ({ id }: { id: number }) => (
      <ComposerDraftContext.Provider value={drafts}><Host onSendMessage={onSendMessage} chatId={id} /></ComposerDraftContext.Provider>)
    const { container, rerender } = render(<App id={1} />)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(6)
    expect(container.querySelector(NOTE)).not.toBeNull()
    rerender(<App id={2} />)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(5)
    expect(container.querySelector(NOTE)).not.toBeNull()
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(sentVideos(onSendMessage)).toHaveLength(5)
  })

  it('adds nothing from a pick that finishes after the chat changed', async () => {
    let finish!: (v: any) => void
    const invoke = vi.fn(() => new Promise(r => { finish = r }))
    ;(window as any).ipc = { pathForFile: () => '', invoke }
    const pickers = React.createRef<ComposerPickers>()
    const drafts = new Map<string, ComposerDraft>()
    const App = ({ id }: { id: number }) => (
      <ComposerDraftContext.Provider value={drafts}><Host onSendMessage={vi.fn()} chatId={id} pickersRef={pickers} /></ComposerDraftContext.Provider>)
    const { container, rerender } = render(<App id={1} />)
    act(() => { pickers.current!.pickVideo() })
    rerender(<App id={2} />)
    await act(async () => { finish([{ path: 'C:/late.mp4', name: 'late.mp4' }]) })
    expect(container.querySelectorAll('.composer-att')).toHaveLength(0)
    rerender(<App id={1} />)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(0)
  })

  it('adds a pick that finishes in the same saved chat', async () => {
    let finish!: (v: any) => void
    const invoke = vi.fn(() => new Promise(r => { finish = r }))
    ;(window as any).ipc = { pathForFile: () => '', invoke }
    const pickers = React.createRef<ComposerPickers>()
    const drafts = new Map<string, ComposerDraft>()
    const { container } = render(
      <ComposerDraftContext.Provider value={drafts}><Host onSendMessage={vi.fn()} chatId={7} pickersRef={pickers} /></ComposerDraftContext.Provider>)
    act(() => { pickers.current!.pickVideo() })
    await act(async () => { finish([{ path: 'C:/a.mp4', name: 'a.mp4' }, { path: 'C:/b.mp4', name: 'b.mp4' }]) })
    expect(container.querySelectorAll('.composer-att')).toHaveLength(2)
  })
})
