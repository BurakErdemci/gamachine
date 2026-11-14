import React, { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AnimatedChatInput, type ComposerPickers } from '../renderer/components/ui/animated-ai-chat'

afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

function Host({ onSendMessage, pickersRef }: { onSendMessage: (...args: any[]) => void; pickersRef?: React.Ref<ComposerPickers> }) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={onSendMessage} isLoading={false} pickersRef={pickersRef} />
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
