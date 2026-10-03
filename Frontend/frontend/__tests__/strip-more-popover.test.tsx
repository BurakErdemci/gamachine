import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ControlPanel } from '../renderer/components/home/ControlPanel'

afterEach(() => cleanup())

function setup() {
  const props = {
    thinkingLevel: 'medium' as const, setThinkingLevel: () => {},
    generationMode: 'auto' as const, setGenerationMode: vi.fn(),
    isAnalyzingProject: false, activeConvId: 7,
    analyzeProject: vi.fn(async () => {}), exportMemory: vi.fn(async () => {}), importMemory: vi.fn(async () => {}),
    compactConversation: async () => {}, isCompacting: false,
    onToggleReports: vi.fn(), reportsOpen: false,
  }
  render(<ControlPanel {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'Diğer ayarlar' }))
  const pop = document.getElementById('strip-more-pop')!
  expect(pop.hidden).toBe(false)
  return { props, pop }
}

it('More settings holds no approval mode list (the top bar chip and Settings set the mode)', () => {
  const { pop } = setup()
  expect(pop.querySelector('[data-mode]')).toBeNull()
  for (const text of ['Modes', 'Otomatik', 'Güvenli Otomatik', 'Adım Adım']) expect(within(pop).queryByText(text)).toBeNull()
  expect(pop.textContent).not.toMatch(/modes/i)
})

it('its remaining items still work: learn project, memory menu, usage report', async () => {
  const { props, pop } = setup()
  fireEvent.click(within(pop).getByRole('button', { name: 'Projeyi Öğren' }))
  expect(props.analyzeProject).toHaveBeenCalledTimes(1)
  const rows = within(pop).getAllByRole('button')
  fireEvent.click(rows.find(b => b.getAttribute('aria-expanded') === 'false')!)
  fireEvent.click(within(pop).getByRole('button', { name: 'Hafızayı Dışarı Aktar' }))
  await waitFor(() => expect(props.exportMemory).toHaveBeenCalledTimes(1))
  fireEvent.click(within(pop).getByTestId('reports-toggle'))
  expect(props.onToggleReports).toHaveBeenCalledTimes(1)
})

it('is drawn on the menu surface tokens with no hard-coded colour', () => {
  const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/thread.css'), 'utf8')
  const rules = css.split('\n').filter(line => line.startsWith('.strip-more-pop')).join('\n')
  expect(rules).toContain('background: var(--menu-bg)')
  expect(rules).toContain('border-color: var(--menu-line)')
  expect(rules).toContain('var(--shell-bg-active)')
  expect(rules).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(/i)
})
