import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

import { ConfirmDialogHost, confirmDialog } from '../renderer/components/ui/ConfirmDialog'

afterEach(cleanup)

const open = async () => {
  render(<ConfirmDialogHost />)
  let answer: boolean | null = null
  await act(async () => { void confirmDialog('Sure?', 'Go', 'No').then(v => { answer = v }) })
  expect(screen.getByText('Sure?')).toBeTruthy()
  return () => answer
}

describe('ConfirmDialog keyboard', () => {
  it('ignores an auto-repeated Enter', async () => {
    const answer = await open()
    await act(async () => { fireEvent.keyDown(window, { key: 'Enter', repeat: true }) })
    expect(answer()).toBeNull()
    expect(screen.getByText('Sure?')).toBeTruthy()
  })

  it('still confirms on a fresh Enter', async () => {
    const answer = await open()
    await act(async () => { fireEvent.keyDown(window, { key: 'Enter' }) })
    expect(answer()).toBe(true)
  })

  it('still cancels on Escape', async () => {
    const answer = await open()
    await act(async () => { fireEvent.keyDown(window, { key: 'Escape' }) })
    expect(answer()).toBe(false)
  })
})
