import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { AppearanceProvider, applyAppearance, DEFAULTS, FONT_STACKS } from '../renderer/lib/appearance'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'

// Round 11: appearance lives on the settings screen's "Görünüm" page.
const props: React.ComponentProps<typeof SettingsScreen> = {
  open: true, page: 'gorunum', providersWithKeys: [], onClose: () => {},
  onLogout: () => {}, onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false,
  onToggleUnityMcp: () => {}, lang: 'en', onLangChange: () => {},
  aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '', thinking_level: 'off' },
}

function settings(lang: Lang = 'en') {
  return render(
    <LangContext.Provider value={{ lang, setLang: () => {}, t: (key, values) => ceviriUygula(lang, key, values) }}>
      <AppearanceProvider><SettingsScreen {...props} lang={lang} /></AppearanceProvider>
    </LangContext.Provider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('ipc', { invoke: vi.fn().mockResolvedValue(true) })
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  applyAppearance(DEFAULTS)
  vi.unstubAllGlobals()
})

describe('appearance settings', () => {
  it.each([['en', 'Workshop'], ['tr', 'Atölye']] as const)('applies and persists the workshop theme in %s', (lang, label) => {
    settings(lang)
    // Theme cards are a radiogroup now; the accessible name is the theme name + its sub line.
    fireEvent.click(screen.getByRole('radio', { name: new RegExp(`^${label}`) }))
    expect(document.documentElement.dataset.theme).toBe('atolye')
    expect(localStorage.getItem('app-theme')).toBe('atolye')
  })

  it('applies Fira Code and removes the override when choosing the theme default', () => {
    settings()
    const select = screen.getByRole('combobox', { name: 'Code font' })
    fireEvent.change(select, { target: { value: 'fira' } })
    expect(document.documentElement.style.getPropertyValue('--font-mono')).toBe(FONT_STACKS.fira)
    expect(localStorage.getItem('app-font-code')).toBe('fira')
    expect(screen.getByRole('option', { name: 'Fira Code' }).style.fontFamily).toBe(FONT_STACKS.fira)
    fireEvent.change(select, { target: { value: 'theme' } })
    expect(document.documentElement.style.getPropertyValue('--font-mono')).toBe('')
  })

  it('updates the reading font, zoom and opening animation preference', () => {
    settings()
    fireEvent.change(screen.getByRole('combobox', { name: 'Reading font' }), { target: { value: 'inter' } })
    expect(document.documentElement.style.getPropertyValue('--font-body')).toBe(FONT_STACKS.inter)
    expect(localStorage.getItem('app-font-reading')).toBe('inter')
    fireEvent.click(screen.getByRole('radio', { name: 'Large' }))
    expect(window.ipc.invoke).toHaveBeenLastCalledWith('app-zoom-set', 1.1)
    expect(localStorage.getItem('app-text-size')).toBe('large')
    fireEvent.click(screen.getByRole('switch', { name: 'Opening animation' }))
    expect(localStorage.getItem('app-intro')).toBe('off')
    expect(screen.getByRole('switch', { name: 'Opening animation' }).getAttribute('aria-checked')).toBe('false')
  })

  it('restores saved preferences on mount', () => {
    localStorage.setItem('app-theme', 'pafta')
    localStorage.setItem('app-font-code', 'fira')
    localStorage.setItem('app-text-size', 'small')
    localStorage.setItem('app-intro', 'off')
    settings()
    expect(document.documentElement.dataset.theme).toBe('pafta')
    expect(document.documentElement.style.getPropertyValue('--font-mono')).toBe(FONT_STACKS.fira)
    expect((screen.getByRole('combobox', { name: 'Code font' }) as HTMLSelectElement).value).toBe('fira')
    expect(window.ipc.invoke).toHaveBeenLastCalledWith('app-zoom-set', 0.9)
    expect(screen.getByRole('switch', { name: 'Opening animation' }).getAttribute('aria-checked')).toBe('false')
  })
})
