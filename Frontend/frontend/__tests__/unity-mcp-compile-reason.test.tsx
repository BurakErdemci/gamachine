import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { cevir } from '../renderer/lib/i18n'

const REASON = 'Unity köprü paketi derlenemedi:\nKeyframeAnimBuilder.cs(319,53): error CS0234: missing type'
const props = { toggling: false, error: null, onToggle: vi.fn() }

afterEach(cleanup)

describe('Unity bridge compile reason', () => {
  it('shows the running reason and opens and closes its popup', () => {
    render(<UnityMcpToggle {...props} status="running" reason={REASON} />)
    const why = screen.getByRole('button', { name: cevir('unity.why') })
    expect(why.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(why)
    expect(why.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('unity-bay').classList.contains('why-open')).toBe(true)
    expect(screen.getByRole('alert').textContent).toContain('KeyframeAnimBuilder.cs(319,53): error CS0234')
    expect(screen.getByRole('alert').textContent).toContain(cevir('unity.compileWhyTitle'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(why.getAttribute('aria-expanded')).toBe('false')
  })

  it('has no reason button or popup when running without a reason', () => {
    render(<UnityMcpToggle {...props} status="running" reason={null} />)
    expect(screen.queryByRole('button', { name: cevir('unity.why') })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('removes the reason when the connection succeeds', () => {
    const { rerender } = render(<UnityMcpToggle {...props} status="running" reason={REASON} />)
    rerender(<UnityMcpToggle {...props} status="connected" reason={REASON} />)
    expect(screen.queryByRole('button', { name: cevir('unity.why') })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps the blocked popup initially open with its existing title', () => {
    render(<UnityMcpToggle {...props} status="blocked" reason="port blocked" />)
    expect(screen.getByRole('button', { name: cevir('unity.why') }).getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('alert').textContent).toContain(cevir('unity.whyTitle'))
    expect(screen.getByRole('alert').textContent).toContain('port blocked')
  })
})
