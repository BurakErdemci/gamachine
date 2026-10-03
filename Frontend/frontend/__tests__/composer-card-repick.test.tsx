/**
 * A welcome card's prompt is applied to the text the composer really holds. Typing never reaches the
 * parent's copy, so before this a card cleared by hand still looked present: picking it again did
 * nothing, and picking another card brought the deleted text back (3 Oct 2026, measured live).
 */
import React, { useState } from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { AnimatedChatInput, type ComposerPickers } from '../renderer/components/ui/animated-ai-chat'
import { questDraft } from '../renderer/components/home/EmptyChat'

afterEach(() => cleanup())

const A = 'Projedeki scriptleri gözden geçir.'
const B = 'Bir hatayı düzeltmeni istiyorum: '

function Host({ handle }: { handle: React.MutableRefObject<ComposerPickers | null> }) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={() => {}} isLoading={false} api="http://127.0.0.1:1" pickersRef={handle} />
}

describe('welcome card picks use the composer text', () => {
  it('fills again after the user deleted the card text, and does not bring deleted text back', () => {
    const handle = { current: null } as React.MutableRefObject<ComposerPickers | null>
    render(<Host handle={handle} />)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    const pick = (prompt: string) => act(() => { handle.current!.editDraft(c => questDraft(c, prompt)) })

    pick(A)
    expect(box.value).toBe(A)
    fireEvent.change(box, { target: { value: '' } })
    pick(A)
    expect(box.value).toBe(A)

    fireEvent.change(box, { target: { value: '' } })
    pick(B)
    expect(box.value).toBe(B)
  })

  it('keeps a typed draft and adds the prompt under it', () => {
    const handle = { current: null } as React.MutableRefObject<ComposerPickers | null>
    render(<Host handle={handle} />)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'kendi notum' } })
    act(() => { handle.current!.editDraft(c => questDraft(c, A)) })
    expect(box.value).toBe(`kendi notum\n${A}`)
  })
})
