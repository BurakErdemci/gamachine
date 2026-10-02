/**
 * The thread header's side-question and branch buttons carry a visible name.
 * Owner report (2 Oct 2026): as bare 14px icons they read as "these features have no UI".
 */
import { describe, it, expect, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

import { SideQuestionButton } from '../renderer/components/home/SideChatPanel'
import { BranchButton } from '../renderer/components/home/ChatTabs'

afterEach(() => cleanup())

describe('thread header actions', () => {
  it('labelled: both buttons show their name as text', () => {
    render(<>
      <SideQuestionButton convId={1} active={false} onOpen={() => {}} labelled />
      <BranchButton sourceId={1} blocked={false} onBranch={async () => {}} labelled />
    </>)
    expect(screen.getByTestId('side-open').textContent).toBe('Yan soru')
    expect(screen.getByTestId('branch-new').textContent).toBe('Dallandır')
  })

  it('unlabelled (tab bar): icon only, as before', () => {
    render(<BranchButton sourceId={1} blocked={false} onBranch={async () => {}} />)
    expect(screen.getByTestId('branch-new').textContent).toBe('')
  })
})
