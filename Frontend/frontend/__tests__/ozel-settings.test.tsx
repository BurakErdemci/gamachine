import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { AppearanceProvider, applyAppearance, DEFAULTS } from '../renderer/lib/appearance'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'
import { OZEL_DEFAULTS, OZEL_STORAGE_KEY, getOzel, isReadable, setOzel } from '../renderer/lib/ozelTheme'

// Round 15: the Özel theme's card and settings group on Görünüm. jsdom has no matchMedia, so Mode
// "Sistem" resolves to the light palette of the active settings.
const props: React.ComponentProps<typeof SettingsScreen> = {
  open: true, page: 'gorunum', providersWithKeys: [], onClose: () => {},
  onLogout: () => {}, onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false,
  onToggleUnityMcp: () => {}, lang: 'tr', onLangChange: () => {},
  aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '', thinking_level: 'off' },
}

function settings(lang: Lang = 'tr') {
  return render(
    <LangContext.Provider value={{ lang, setLang: () => {}, t: (key, values) => ceviriUygula(lang, key, values) }}>
      <AppearanceProvider><SettingsScreen {...props} lang={lang} /></AppearanceProvider>
    </LangContext.Provider>,
  )
}
const root = document.documentElement
const group = () => screen.getByRole('region', { name: 'Özel tema ayarları' })
const hex = (name: string) => screen.getByRole('textbox', { name: `${name}, onaltılık renk (#RRGGBB)` }) as HTMLInputElement
const pickOzel = () => fireEvent.click(screen.getByRole('radio', { name: /^Özel/ }))

beforeEach(() => {
  localStorage.clear()
  setOzel(OZEL_DEFAULTS)
  localStorage.clear()
  vi.stubGlobal('ipc', { invoke: vi.fn().mockResolvedValue(true) })
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  applyAppearance(DEFAULTS)
  vi.unstubAllGlobals()
})

describe('Özel theme settings', () => {
  it('the Özel card is the fifth radio and selects the theme', () => {
    settings()
    const cards = screen.getAllByRole('radio').filter(r => r.classList.contains('theme-card'))
    expect(cards).toHaveLength(5)
    expect(cards[4].getAttribute('data-theme-pick')).toBe('ozel')
    expect(cards[4].textContent).toContain('şu an Mono')
    pickOzel()
    expect(root.dataset.theme).toBe('ozel')
    expect(localStorage.getItem('app-theme')).toBe('ozel')
    expect(cards[4].getAttribute('aria-checked')).toBe('true')
    expect(group()).toBeTruthy()
    // Mono, System mode, no system dark -> the light Mono palette
    expect(root.style.getPropertyValue('--u-bg')).toBe('#FFFFFF')
    expect(root.style.getPropertyValue('--u-fg')).toBe('#000000')
  })

  it('the group is hidden for the four character themes', () => {
    settings()
    expect(screen.queryByRole('region', { name: 'Özel tema ayarları' })).toBeNull()
    pickOzel()
    expect(group()).toBeTruthy()
    for (const name of [/^Arena/, /^Sade/, /^Pafta/, /^Atölye/]) {
      fireEvent.click(screen.getByRole('radio', { name }))
      expect(screen.queryByRole('region', { name: 'Özel tema ayarları' })).toBeNull()
      expect(root.getAttributeNames().filter(n => n.startsWith('data-u-'))).toEqual([])
      expect(root.style.getPropertyValue('--u-bg')).toBe('')
    }
  })

  it('preset buttons write the palette, font and persist', () => {
    settings()
    pickOzel()
    const presets = within(group()).getByRole('radiogroup', { name: 'Hazır tema' })
    fireEvent.click(within(presets).getByRole('radio', { name: 'Kâğıt' }))
    expect(within(presets).getByRole('radio', { name: 'Kâğıt' }).getAttribute('aria-checked')).toBe('true')
    expect(root.style.getPropertyValue('--u-bg')).toBe('#F3F0E8')
    expect(root.style.getPropertyValue('--u-fg')).toBe('#1F1D1A')
    expect(root.style.getPropertyValue('--u-font-ui')).toContain('IBM Plex Mono')
    expect(hex('Arka plan').value).toBe('#F3F0E8')
    expect(JSON.parse(localStorage.getItem(OZEL_STORAGE_KEY)!)).toMatchObject({ preset: 'kagit', mode: 'system' })
    // keyboard: arrows move and select inside the group
    const kagit = within(presets).getByRole('radio', { name: 'Kâğıt' })
    kagit.focus()
    fireEvent.keyDown(kagit, { key: 'ArrowRight' })
    expect(getOzel().preset).toBe('gece')
    expect(document.activeElement).toBe(within(presets).getByRole('radio', { name: 'Gece' }))
    // Mode "Koyu" switches to the dark palette of the same preset
    fireEvent.click(within(group()).getByRole('radio', { name: 'Koyu' }))
    expect(root.style.getPropertyValue('--u-bg')).toBe('#0D1117')
  })

  it('the hex input validates: an invalid value is marked and changes nothing; a valid one applies live', () => {
    settings()
    pickOzel()
    const bg = hex('Arka plan')
    fireEvent.change(bg, { target: { value: '#12zz' } })
    expect(bg.getAttribute('aria-invalid')).toBe('true')
    expect(root.style.getPropertyValue('--u-bg')).toBe('#FFFFFF')
    fireEvent.change(bg, { target: { value: '#123456' } })
    expect(bg.getAttribute('aria-invalid')).toBeNull()
    expect(root.style.getPropertyValue('--u-bg')).toBe('#123456')
    expect(getOzel().preset).toBeNull()
    expect(screen.getByRole('radio', { name: /^Özel/ }).textContent).toContain('şu an Mono (değiştirildi)')
    fireEvent.change(bg, { target: { value: 'abc' } })
    fireEvent.blur(bg)
    expect(bg.value).toBe('#AABBCC')
  })

  it('warns for #777 on #888, the fix clears it, Geri al restores', () => {
    settings()
    pickOzel()
    fireEvent.change(hex('Arka plan'), { target: { value: '#888888' } })
    fireEvent.change(hex('Ön plan'), { target: { value: '#777777' } })
    const guard = screen.getByTestId('oz-guard')
    expect(guard.hidden).toBe(false)
    expect(guard.textContent).toContain('Yazı bu zeminde zor okunuyor')
    expect(root.hasAttribute('data-u-bad')).toBe(true)

    fireEvent.click(within(guard).getByRole('button', { name: 'Okunur tona çek' }))
    expect(root.hasAttribute('data-u-bad')).toBe(false)
    const fg = root.style.getPropertyValue('--u-fg'), bgNow = root.style.getPropertyValue('--u-bg')
    expect(isReadable(fg, bgNow)).toBe(true)
    expect(guard.classList.contains('is-fixed')).toBe(true)
    expect(guard.textContent).toContain('her yazı artık en az 4,5:1')
    const undo = within(guard).getByRole('button', { name: 'Geri al' })
    expect(document.activeElement).toBe(undo)

    fireEvent.click(undo)
    expect(root.style.getPropertyValue('--u-fg')).toBe('#777777')
    expect(root.style.getPropertyValue('--u-bg')).toBe('#888888')
    expect(root.hasAttribute('data-u-bad')).toBe(true)
    expect(guard.textContent).toContain('zor okunuyor')
  })

  it('import with a broken text shows the error and changes nothing; a valid text applies', () => {
    settings()
    pickOzel()
    const before = JSON.stringify(getOzel())
    const stored = localStorage.getItem(OZEL_STORAGE_KEY)
    fireEvent.click(screen.getByRole('button', { name: 'İçe aktar' }))
    const box = screen.getByRole('textbox', { name: 'Tema metnini yapıştır' })
    expect(document.activeElement).toBe(box)
    fireEvent.change(box, { target: { value: 'gm-tema:1;ad=Neon;bg=#000000;fg=#FFFFFF;vurgu=#39FF1G' } })
    expect(box.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByText('vurgu: “#39FF1G” bir renk değil. Örnek: #1A2B3C.')).toBeTruthy()
    const apply = screen.getByRole('button', { name: 'Uygula' })
    expect(apply.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(apply)
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(JSON.stringify(getOzel())).toBe(before)
    expect(localStorage.getItem(OZEL_STORAGE_KEY)).toBe(stored)
    expect(root.style.getPropertyValue('--u-accent')).toBe('#FFFFFF')

    // Esc closes only the box
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(box.closest('.oz-imp')!.hasAttribute('hidden')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'İçe aktar' }))
    fireEvent.change(box, { target: { value: 'gm-tema:1;ad=Kömür;bg=#1C1C1C;fg=#EEEEEE;vurgu=#39FF14;yazi=jetbrains-mono' } })
    expect(apply.getAttribute('aria-disabled')).toBe('false')
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(root.style.getPropertyValue('--u-bg')).toBe('#1C1C1C')
    expect(root.style.getPropertyValue('--u-accent')).toBe('#39FF14')
    expect(root.style.getPropertyValue('--u-font-ui')).toContain('JetBrains Mono')
    expect(getOzel()).toMatchObject({ preset: null, name: 'Kömür', font: 'jetbrains-mono' })
    expect(screen.getByRole('radio', { name: /^Özel/ }).textContent).toContain('Kömür (değiştirildi)')
  })

  it('copies the theme text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    settings()
    pickOzel()
    fireEvent.click(screen.getByRole('button', { name: 'Kopyala' }))
    expect(writeText).toHaveBeenCalledWith('gm-tema:1;ad=Mono;bg=#FFFFFF;fg=#000000;vurgu=#FFFFFF;yazi=geist-mono')
    expect(screen.getByRole('button', { name: 'Kopyalandı' })).toBeTruthy()
  })

  it('long answers, interface font and the status legend', () => {
    settings()
    pickOzel()
    expect(root.hasAttribute('data-u-mono')).toBe(true)
    fireEvent.click(within(group()).getByRole('radio', { name: 'Okuma yazı tipi' }))
    expect(root.hasAttribute('data-u-mono')).toBe(false)
    expect(getOzel().read).toBe('read')
    fireEvent.change(within(group()).getByRole('combobox', { name: 'Arayüz yazı tipi' }), { target: { value: 'inter' } })
    expect(root.style.getPropertyValue('--u-font-ui')).toContain('Inter')
    expect(within(group()).getByText('Bekliyor')).toBeTruthy()
    expect(within(group()).getByText('Hata')).toBeTruthy()
  })

  it('persists across a reload of the module', async () => {
    settings()
    pickOzel()
    fireEvent.click(within(group()).getByRole('radio', { name: 'Gece' }))
    fireEvent.change(hex('Vurgu'), { target: { value: '#39FF14' } })
    cleanup()
    vi.resetModules()
    const fresh = await import('../renderer/lib/ozelTheme')
    expect(fresh.getOzel()).toMatchObject({ preset: null, mode: 'system', font: 'inter', light: { bg: '#F5F7FA', fg: '#18202B', accent: '#39FF14' } })
    const appearance = await import('../renderer/lib/appearance')
    expect(appearance.loadAppearance().theme).toBe('ozel')
  })

  it('every new string exists in English too', () => {
    settings('en')
    act(() => { fireEvent.click(screen.getByRole('radio', { name: /^Custom/ })) })
    expect(screen.getByRole('region', { name: 'Custom theme settings' })).toBeTruthy()
    expect(screen.getByRole('radio', { name: 'Paper' })).toBeTruthy()
  })
})
