import React, { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'

afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

function Host({ onSendMessage }: { onSendMessage: (...args: any[]) => void }) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={onSendMessage} isLoading={false} />
}

function dropFile(box: HTMLElement, file: File) {
  fireEvent.drop(box, { dataTransfer: { getData: () => '', files: [file] } })
}

function setup(pathForFile: (f: File) => string) {
  ;(window as any).ipc = { pathForFile }
  const onSendMessage = vi.fn()
  const { container } = render(<Host onSendMessage={onSendMessage} />)
  const box = screen.getByRole('textbox') as HTMLTextAreaElement
  return { onSendMessage, container, box }
}

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
})
