/**
 * A fenced block without a language (Suno style / lyrics blocks) had no copy button:
 * only language-tagged blocks get the CodeBlock header (Burak, 4 Nov 2026).
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MarkdownRenderer } from '../renderer/components/home/MarkdownRenderer'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const mockClipboard = () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  return writeText
}

describe('copy button of a block without a language', () => {
  it('copies the whole block text, every line, without the trailing newline', () => {
    const writeText = mockClipboard()
    const body = '[Intro - pings]\n[Main Groove - full band]\n[Stop - one bar]'
    const { container } = render(<MarkdownRenderer content={'Lyrics\n\n```\n' + body + '\n```\n'} />)
    const buttons = container.querySelectorAll('.md-pre-copy')
    expect(buttons.length).toBe(1)
    fireEvent.click(buttons[0])
    expect(writeText).toHaveBeenCalledWith(body)
  })

  it('gives each plain block its own button', () => {
    const writeText = mockClipboard()
    const { container } = render(<MarkdownRenderer content={'```\nrestless garage punk\n```\n\n```\nsad, ballad\n```\n'} />)
    const buttons = container.querySelectorAll('.md-pre-copy')
    expect(buttons.length).toBe(2)
    fireEvent.click(buttons[1])
    expect(writeText).toHaveBeenCalledWith('sad, ballad')
  })

  it('leaves a language-tagged block to the CodeBlock header button alone', () => {
    mockClipboard()
    const { container } = render(<MarkdownRenderer content={'```python\nprint(1)\n```\n'} />)
    expect(container.querySelectorAll('.md-pre-copy').length).toBe(0)
    expect(screen.getAllByRole('button').length).toBeGreaterThan(0)
  })

  it('puts no button on inline code', () => {
    mockClipboard()
    const { container } = render(<MarkdownRenderer content={'run `npm test` now'} />)
    expect(container.querySelectorAll('.md-pre-copy').length).toBe(0)
  })
})
