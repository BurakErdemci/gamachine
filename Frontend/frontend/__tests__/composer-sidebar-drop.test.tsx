import React, { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'

afterEach(() => cleanup())

const entry = { path: 'Assets/Example.cs', name: 'Example.cs', isDirectory: false }
const marker = `[File Attached: ${entry.path}]`

function Host({ onSendMessage }: { onSendMessage: (message: string) => void }) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={onSendMessage} isLoading={false} />
}

function dropSidebarFile(box: HTMLElement) {
  fireEvent.drop(box, {
    dataTransfer: {
      getData: (type: string) => type === 'application/x-gamachine-file' ? JSON.stringify(entry) : '',
      files: [],
    },
  })
}

describe('sidebar file drops', () => {
  it('keeps the draft unchanged and sends one file marker, even after a repeated drop', () => {
    const onSendMessage = vi.fn()
    const { container } = render(<Host onSendMessage={onSendMessage} />)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'Review this file' } })

    dropSidebarFile(box)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(1)
    expect(screen.getByText(entry.name)).toBeTruthy()
    expect(box.value).toBe('Review this file')

    dropSidebarFile(box)
    expect(container.querySelectorAll('.composer-att')).toHaveLength(1)
    expect(box.value).toBe('Review this file')

    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledTimes(1)
    const message = onSendMessage.mock.calls[0][0] as string
    expect(message).toBe(`Review this file\n\n${marker}`)
    expect(message.split(marker)).toHaveLength(2)
  })

  it('sends no file marker after the chip is removed', async () => {
    const onSendMessage = vi.fn()
    render(<Host onSendMessage={onSendMessage} />)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'Review this file' } })
    dropSidebarFile(box)
    expect(box.value).toBe('Review this file')

    const chip = screen.getByText(entry.name).closest('.composer-att') as HTMLElement
    fireEvent.click(within(chip).getByRole('button'))
    await waitFor(() => expect(screen.queryByText(entry.name)).toBeNull())

    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledTimes(1)
    expect(onSendMessage.mock.calls[0][0]).toBe('Review this file')
    expect(onSendMessage.mock.calls[0][0]).not.toContain('[File Attached:')
  })
})
