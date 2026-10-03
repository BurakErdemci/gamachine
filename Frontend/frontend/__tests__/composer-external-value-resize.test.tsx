/**
 * A welcome card puts its prompt into the composer through the `value` prop, not by typing.
 * The box has to grow for that text too: it stayed one line high and, with overflow hidden,
 * the rest of the prompt could neither be seen nor scrolled to (3 Oct 2026).
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'

// jsdom has no layout: report a content height that follows the text length.
Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
  configurable: true,
  get(this: HTMLTextAreaElement) { return this.value.length > 40 ? 96 : 36 },
})

afterEach(() => cleanup())

const ui = (value: string) => (
  <AnimatedChatInput value={value} setValue={() => {}} onSendMessage={() => {}} isLoading={false} api="http://127.0.0.1:1" />
)

describe('composer height follows text set from outside', () => {
  it('grows when a card fills the box and shrinks back when it is cleared', () => {
    const { rerender } = render(ui(''))
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    rerender(ui('Projedeki scriptleri gözden geçir; riskli yerleri ve olası hataları tek listede göster.'))
    expect(box.style.height).toBe('96px')
    rerender(ui(''))
    expect(box.style.height).toBe('36px')
  })
})
