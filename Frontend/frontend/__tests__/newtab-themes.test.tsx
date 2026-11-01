import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { NewTabPanel } from '../renderer/components/home/newtab/NewTabPanel'
import { AppearanceContext, DEFAULTS, THEMES, type Theme } from '../renderer/lib/appearance'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'
import type { Site } from '../renderer/lib/browserSuggest'

const en = translations.en

// One piece only each theme's own composition draws: a recoloured shared grid would fail this.
const SIGNATURE: Record<Theme, string> = {
  arena: '.nt-ar-hud .nt-ar-meter',
  sade: '.nt-sd-col .nt-sd-hint',
  pafta: '.nt-pf-sheet .nt-pf-tb',
  atolye: '.nt-at-rail .nt-at-title',
  ozel: '.nt-oz-head .brand-head',
}
const SITE_SIGNATURE: Record<Theme, string> = {
  arena: '.nt-ar-hero',
  sade: '.nt-sd-row',
  pafta: '.nt-pf-detail',
  atolye: '.nt-at-ticket',
  ozel: '.nt-oz-word',
}
const EMPTY: Record<Theme, string> = {
  arena: en['nt.arena.empty'],
  sade: en['bp.suggestedEmpty'],
  pafta: en['nt.pafta.empty'],
  atolye: en['nt.atolye.empty'],
  ozel: en['bp.suggestedEmpty'],
}

const SITES: Site[] = [
  { url: 'https://itch.io/game-assets', label: 'itch.io', favicon: '' },
  { url: 'https://docs.unity3d.com/Manual/index.html', label: 'docs.unity3d.com', favicon: 'data:image/png;base64,AAAA' },
  { url: 'https://github.com/', label: 'github.com', favicon: '' },
]

function view(theme: Theme, sites: Site[], onPick = vi.fn()) {
  const lang = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  const utils = render(
    <AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, theme }, setAppearance: () => {} }}>
      <LangContext.Provider value={lang}>
        <NewTabPanel sites={sites} onPick={onPick} />
      </LangContext.Provider>
    </AppearanceContext.Provider>,
  )
  return { ...utils, onPick }
}

afterEach(cleanup)

describe('empty tab: one composition per theme', () => {
  it.each(THEMES)('%s renders its own layout with the sites, most visited first', theme => {
    const { container } = view(theme, SITES)
    const roots = container.querySelectorAll('[data-nt]')
    expect(roots).toHaveLength(1)
    expect(roots[0].getAttribute('data-nt')).toBe(theme)
    expect(container.querySelector(SIGNATURE[theme])).toBeTruthy()
    for (const other of THEMES.filter(t => t !== theme)) expect(container.querySelector(SIGNATURE[other])).toBeNull()
    expect(container.querySelector(SITE_SIGNATURE[theme])).toBeTruthy()
    expect([...container.querySelectorAll('.nt-name')].map(n => n.textContent)).toEqual(SITES.map(s => s.label))
  })

  it.each(THEMES)('%s: every site is a keyboard-reachable button titled with its URL, and a click picks that URL', theme => {
    const { container, onPick } = view(theme, SITES)
    const buttons = [...container.querySelectorAll<HTMLButtonElement>('button.nt-site')]
    expect(buttons.map(b => b.title)).toEqual(SITES.map(s => s.url))
    for (const b of buttons) {
      expect(b.getAttribute('type')).toBe('button')
      expect(b.disabled).toBe(false)
      expect(b.tabIndex).toBe(0)
    }
    fireEvent.click(screen.getByRole('button', { name: /docs\.unity3d\.com/ }))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('https://docs.unity3d.com/Manual/index.html')
  })

  it.each(THEMES)('%s with no sites shows its own empty state and no site buttons', theme => {
    const { container, onPick } = view(theme, [])
    expect(container.querySelector('[data-nt]')!.getAttribute('data-nt')).toBe(theme)
    expect(container.querySelector(SIGNATURE[theme])).toBeTruthy()
    expect(screen.getByText(EMPTY[theme])).toBeTruthy()
    expect(container.querySelectorAll('button.nt-site')).toHaveLength(0)
    expect(onPick).not.toHaveBeenCalled()
  })

  it('the theme prop overrides the appearance, like HubPanel', () => {
    const lang = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
    const { container } = render(
      <AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, theme: 'arena' }, setAppearance: () => {} }}>
        <LangContext.Provider value={lang}><NewTabPanel theme="pafta" sites={SITES} onPick={vi.fn()} /></LangContext.Provider>
      </AppearanceContext.Provider>,
    )
    expect(container.querySelector('[data-nt]')!.getAttribute('data-nt')).toBe('pafta')
  })

  it('Arena fills one slot per site and shows the count; Atölye numbers its orders', () => {
    const arena = view('arena', SITES)
    expect(arena.container.querySelectorAll('.nt-ar-pips i.is-on')).toHaveLength(3)
    expect(arena.container.querySelector('.nt-ar-count')!.textContent).toBe('3/8')
    cleanup()
    const atolye = view('atolye', SITES)
    expect([...atolye.container.querySelectorAll('.nt-at-stub')].map(s => s.textContent)).toEqual(['No. 01', 'No. 02', 'No. 03'])
  })

  it('every nt.* string has an English and a Turkish text', () => {
    const keys = Object.keys(translations.tr).filter(k => k.startsWith('nt.'))
    expect(keys.length).toBeGreaterThan(0)
    for (const k of keys) {
      expect(translations.tr[k]).toBeTruthy()
      expect(translations.en[k]).toBeTruthy()
    }
  })
})
