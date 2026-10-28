import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { cevir, ceviriUygula, LangContext } from '../renderer/lib/i18n'

const diagnostic = { reasonCode: 'bridge_compile_failed', reasonLines: ['KeyframeAnimBuilder.cs(319,53): error CS0234: missing type'] }
const props = { toggling: false, error: null, onToggle: vi.fn() }

afterEach(cleanup)

describe('Unity bridge compile reason', () => {
  it('shows the running reason and opens and closes its popup', () => {
    render(<UnityMcpToggle {...props} {...diagnostic} status="running" reason={null} slowConnect />)
    expect(screen.getByTestId('unity-bay').classList.contains('has-reason')).toBe(true)
    expect(document.querySelector('.unity-hint')).toBeNull()
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
    expect(screen.getByTestId('unity-bay').classList.contains('has-reason')).toBe(false)
  })

  it('removes the reason when the connection succeeds', () => {
    const { rerender } = render(<UnityMcpToggle {...props} {...diagnostic} status="running" reason={null} />)
    rerender(<UnityMcpToggle {...props} {...diagnostic} status="connected" reason={null} />)
    expect(screen.queryByRole('button', { name: cevir('unity.why') })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByTestId('unity-bay').classList.contains('has-reason')).toBe(false)
  })

  it('keeps the blocked popup initially open with its existing title', () => {
    render(<UnityMcpToggle {...props} status="blocked" reason="port blocked" />)
    expect(screen.getByTestId('unity-bay').classList.contains('has-reason')).toBe(true)
    expect(screen.getByRole('button', { name: cevir('unity.why') }).getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('alert').textContent).toContain(cevir('unity.whyTitle'))
    expect(screen.getByRole('alert').textContent).toContain('port blocked')
  })

  it.each(['tr', 'en'] as const)('translates the heading to %s and preserves compiler lines', lang => {
    const t = (key: Parameters<typeof cevir>[0]) => ceviriUygula(lang, key)
    render(<LangContext.Provider value={{ lang, setLang: vi.fn(), t }}>
      <UnityMcpToggle {...props} {...diagnostic} status="running" reason={null} />
    </LangContext.Provider>)
    const popup = screen.getByRole('alert')
    expect(popup.querySelector('b')?.textContent).toBe(lang === 'tr'
      ? 'Unity köprü paketi derlenemedi.' : 'The Unity bridge package failed to compile.')
    expect(popup.textContent).toContain(diagnostic.reasonLines[0])
  })
})
