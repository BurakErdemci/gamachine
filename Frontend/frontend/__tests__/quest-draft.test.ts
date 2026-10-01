/**
 * A mission-board card fills the composer. It used to replace the composer's text, so a draft
 * the user had typed was lost to one click (P2 audit). home.tsx's pickQuest goes through
 * questDraft.
 */
import { describe, it, expect } from 'vitest'
import { questDraft } from '../renderer/components/home/EmptyChat'

describe('questDraft', () => {
  it('fills an empty composer with the starting prompt', () => {
    expect(questDraft('', 'Explore the project')).toBe('Explore the project')
    expect(questDraft('  \n', 'Explore the project')).toBe('Explore the project')
  })

  it('keeps typed text and puts the prompt under it', () => {
    expect(questDraft('my half-written idea  ', 'Explore the project'))
      .toBe('my half-written idea\nExplore the project')
  })

  it('does not stack the same card twice', () => {
    const once = questDraft('draft', 'Explore the project')
    expect(questDraft(once, 'Explore the project')).toBe(once)
  })
})
