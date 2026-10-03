import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, within, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { ModelSelector } from '../renderer/components/home/ModelSelector'
import { UseBlock } from '../renderer/components/home/UsageMeters'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { resetLabel, type UsageFamily } from '../renderer/lib/usageLimits'

const NOW = '2026-10-02T12:00:00Z'
const FAM: UsageFamily = {
  family: 'claude', status: 'ok', plan: 'Max', measured_at: NOW, stale: false, error: null,
  windows: [
    { id: '5h', group: null, label: '5h', kind: '5h', used_pct: 85, resets_at: '2026-10-02T21:15:00Z', resets_text: null },
    { id: 'week', group: null, label: 'week', kind: 'week', used_pct: 100, resets_at: '2026-10-08T11:30:00Z', resets_text: null },
    { id: 'fallback', group: null, label: 'fallback', kind: 'week', used_pct: 44, resets_at: null, resets_text: 'Thursday 8 October 2026 at 14:30 (local time)' },
  ],
}
const gm = (file: string) => readFileSync(resolve(__dirname, '../renderer/styles/gm', file), 'utf8')
const settings = gm('settings.css')
const css = ['shell.css', 'theme-arena.shell.css', 'theme-sade.shell.css', 'theme-pafta.shell.css',
  'theme-atolye.shell.css', 'settings.css', 'theme-ozel.shell.css'].map(gm).join('\n').replace(/\/\*[\s\S]*?\*\//g, '')

// Resolve the simple class/attribute rules used by these controls, including theme overrides.
const declarations = (element: Element) => {
  const matches = Array.from(css.matchAll(/([^{}]+)\{([^{}]*)\}/g)).flatMap(([, selectors, body]) =>
    selectors.split(',').filter(selector => {
      try { return element.matches(selector.trim()) } catch { return false }
    }).map(selector => ({ body, weight: (selector.match(/\.[\w-]+|\[[^\]]+\]|:[\w-]+/g) || []).length })))
  const result: Record<string, string> = {}
  for (const { body } of matches.sort((a, b) => a.weight - b.weight)) {
    for (const declaration of body.split(';')) {
      const at = declaration.indexOf(':')
      if (at >= 0) result[declaration.slice(0, at).trim()] = declaration.slice(at + 1).trim()
    }
  }
  return result
}

afterEach(() => { cleanup(); aktifDilAyarla('tr'); document.documentElement.removeAttribute('data-theme') })

describe.each(['tr', 'en'] as const)('full usage reset text (%s)', lang => {
  it.each(['mm-use', 'use-block'])('keeps the time, weekday and fallback in %s, with a full title', className => {
    aktifDilAyarla(lang)
    for (const win of FAM.windows) {
      render(<UseBlock fam={{ ...FAM, windows: [win] }} nowIso={NOW} className={className} />)
      const row = screen.getAllByTestId('use-row').find(row => row.querySelector('.use-r')?.textContent)!
      const full = cevir('use.resets', { zaman: resetLabel(win, lang, NOW) })
      expect(row.querySelector('.use-r')!.textContent).toBe(full)
      expect(row.getAttribute('title')).toContain(full)
      expect(full).not.toContain('…')
      if (win.id === 'week') expect(full).toMatch(/\S+ \S+ \d\d:\d\d$/)
      if (win.id === 'fallback') expect(full).toContain(win.resets_text)
      cleanup()
    }
  })
})

describe.each(['arena', 'sade', 'pafta', 'atolye', 'ozel'])('overflow CSS (%s)', theme => {
  it('allows full reset text in both the menu and settings', () => {
    document.documentElement.setAttribute('data-theme', theme)
    render(<><div className="model-menu"><UseBlock fam={FAM} nowIso={NOW} className="mm-use" /></div>
      <div className="set-main"><UseBlock fam={FAM} nowIso={NOW} /></div></>)
    for (const row of screen.getAllByTestId('use-row')) {
      const reset = declarations(row.querySelector('.use-r')!)
      expect(reset['white-space']).toBe('normal')
      expect(reset['overflow-wrap']).toBe('anywhere')
      expect(reset.overflow === 'hidden' && reset['text-overflow'] === 'ellipsis' && reset['white-space'] === 'nowrap').toBe(false)
      if (row.closest('.mm-use')) {
        expect(reset['grid-column']).toBe('2 / -1')
        expect(declarations(row)['grid-template-columns']).toBe('46px minmax(0, 1fr) auto')
      }
    }
  })

  it('lets the label and control wrap, and the buttons wrap inside the available width', () => {
    document.documentElement.setAttribute('data-theme', theme)
    render(<div className="model-menu"><div className="mm-effort"><span className="mm-effort-k">Düşünme</span>
      <span className="gm-seg gm-seg-sm"><button>XHigh</button><button>Max</button></span></div></div>)
    const row = document.querySelector('.mm-effort')!
    const group = row.querySelector('.gm-seg-sm')!
    expect(declarations(row)['flex-wrap']).toBe('wrap')
    expect(declarations(row)['min-width']).toBe('0')
    expect(declarations(group)['flex-wrap']).toBe('wrap')
    expect(declarations(group)['max-width']).toBe('100%')
    expect(declarations(group).flex).toBe('0 1 auto')
    expect(declarations(group.querySelector('button')!).flex).toBe('1 0 auto')
  })
})

it('stacks the menu columns at narrow menu widths', () => {
  expect(settings).toMatch(/\.model-menu\s*\{[^}]*container-type:\s*inline-size/)
  expect(settings).toMatch(/@container\s*\(max-width:\s*520px\)\s*\{\s*\.mm-body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  expect(settings).toMatch(/@container[\s\S]*\.mm-pane,\s*\.mm-effort\s*\{\s*grid-column:\s*1/)
})

const EFFORT_LISTS = [
  ['auto'], ['auto', 'off', 'low', 'high'], ['auto', 'none', 'minimal', 'low', 'medium', 'high'],
  ['auto', 'low', 'medium', 'high', 'xhigh'], ['auto', 'low', 'medium', 'high', 'xhigh', 'max'],
]
describe.each(['tr', 'en'] as const)('effort radios (%s)', lang => {
  it.each(EFFORT_LISTS.map(levels => ({ levels })))('renders every option in $levels and preserves selection', ({ levels }) => {
    aktifDilAyarla(lang)
    const onThinkingChange = vi.fn()
    render(<ModelSelector
      aiConfig={{ provider_type: 'subscription', model_name: 'claude-opus-5-5', api_key: '', thinking_level: 'high' }}
      setAiConfig={vi.fn()} availableModels={{ subscription: [], local: [], cloud: [] }} providersWithKeys={[]}
      effectiveProvider="subscription" displayModelName="Claude Opus 5.5" isModelDropdownOpen
      setIsModelDropdownOpen={vi.fn()} modelOrToggles={{}} setModelOrToggles={vi.fn()} user={null}
      fetchAvailableModels={vi.fn()} setShowSettings={vi.fn()} API="" axios={{}} showToast={vi.fn()}
      thinkingLevel="auto" effortLevels={levels} onThinkingChange={onThinkingChange}
    />)
    const row = screen.getByTestId('mm-effort')
    expect(row.getAttribute('data-guide')).toBe('model-effort')
    const group = within(row).getByRole('radiogroup', { name: cevir('mm.effortGroup') })
    const radios = within(group).getAllByRole('radio')
    expect(radios.map(radio => radio.textContent)).toEqual(levels.map(level => cevir(`effort.label.${level}` as any)))
    expect(radios[0].getAttribute('aria-checked')).toBe('true')
    for (let i = 0; i < radios.length; i++) {
      expect(radios[i].getAttribute('type')).toBe('button')
      expect((radios[i] as HTMLButtonElement).disabled).toBe(levels.length === 1)
      if (levels.length > 1) {
        expect(radios[i].tabIndex).toBe(0)
        fireEvent.click(radios[i])
        expect(onThinkingChange).toHaveBeenLastCalledWith(levels[i])
      }
    }
  })
})
