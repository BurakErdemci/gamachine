/**
 * A half-streamed markdown link must not show its raw syntax or its URL.
 *
 * Fault measured on Gamachine 4.0.0 (12 Nov 2026): while an answer streamed,
 * `**[Tune.cs](file:///C:/Unity%20Projeler/...` appeared as literal text in two
 * chats, putting the full local path on screen recordings. Until the closing
 * `)` arrives the text is not a link to the markdown parser, so it renders as
 * plain characters, URL included.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

import { MarkdownRenderer } from '../renderer/components/home/MarkdownRenderer'

afterEach(() => cleanup())

const PARTIAL = 'Updated the tuning.\n\nSee **[Tune.cs](file:///C:/Unity%20Projeler/Proj/Assets/Scr'
const DONE = 'Updated the tuning.\n\nSee **[Tune.cs](file:///C:/Unity%20Projeler/Proj/Assets/Scripts/Tune.cs)** now.'

const Md = MarkdownRenderer as unknown as React.FC<{ content: string; streaming?: boolean }>

describe('MarkdownRenderer while streaming', () => {
  it('an unfinished link shows neither raw syntax nor its path', () => {
    const { container } = render(<Md content={PARTIAL} streaming />)
    const text = container.textContent || ''
    expect(text).toContain('Tune.cs')
    expect(text).not.toContain('file:')
    expect(text).not.toContain('](')
    expect(text).not.toContain('[')
    expect(text).not.toContain('**')
    expect(container.innerHTML).not.toContain('Projeler')
  })

  it('an unfinished image shows no path either', () => {
    const { container } = render(<Md content={'Shot:\n\n![scene](file:///C:/Unity%20Projeler/P/shot.pn'} streaming />)
    expect(container.innerHTML).not.toContain('Projeler')
    expect(container.textContent).not.toContain('![')
  })

  it('a finished link renders exactly as without streaming', () => {
    const a = render(<Md content={DONE} streaming />).container.innerHTML
    cleanup()
    const b = render(<Md content={DONE} />).container.innerHTML
    expect(a).toBe(b)
    expect(a).toContain('<strong><a href="file:///C:/Unity%20Projeler/Proj/Assets/Scripts/Tune.cs"')
  })

  it('without the streaming flag the text is left as it is', () => {
    const { container } = render(<Md content={PARTIAL} />)
    expect(container.textContent).toContain('](file:')
  })
})
