import React from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { EmptyChat } from '../renderer/components/home/EmptyChat'
import { tr } from '../renderer/lib/i18n'

// The greeting and the writing cards must promise what the approval mode does: auto mode never
// asks, so "I ask before I change anything" there was a false claim (Burak, 2 Oct 2026).
describe('EmptyChat · promise follows the approval mode', () => {
  const cases = [
    ['step', 'empty.sub', 'quest.asks'],
    ['balanced', 'empty.subBalanced', 'quest.asksBalanced'],
    ['auto', 'empty.subAuto', 'quest.asksAuto'],
  ] as const

  for (const [mode, sub, asks] of cases) {
    it(`${mode}: greeting and card foot use the ${mode} copy`, () => {
      const { container } = render(<EmptyChat projectName="P" onPick={vi.fn()} approvalMode={mode} />)
      expect(container.querySelector('.empty-sub .lex-d')?.textContent).toBe(tr[sub])
      expect(screen.getAllByText(tr[asks]).length).toBe(2)
    })
  }

  it('auto mode never says it asks first', () => {
    const { container } = render(<EmptyChat projectName="P" onPick={vi.fn()} approvalMode="auto" />)
    expect(container.textContent).not.toContain(tr['quest.asks'])
    expect(container.textContent).not.toContain('sorarım')
  })

  it('without a mode it keeps the strictest (step) promise', () => {
    const { container } = render(<EmptyChat projectName="P" onPick={vi.fn()} />)
    expect(container.querySelector('.empty-sub .lex-d')?.textContent).toBe(tr['empty.sub'])
  })
})
