import React from 'react'
import { renderToString } from 'react-dom/server'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ComputerLivePage from '../renderer/pages/computer-live'

describe('computer-live page', () => {
  it('renders no language-dependent markup on the server, then the tag after mount', async () => {
    expect(renderToString(<ComputerLivePage />)).not.toContain('cul-tag')
    const { container } = render(<ComputerLivePage />)
    await screen.findAllByRole('button')
    expect(container.querySelector('.cul-tag')).not.toBeNull()
  })
})
