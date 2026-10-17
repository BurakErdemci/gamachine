/**
 * The language choice on the tour's name step (owner, 16 Oct 2026): the same segmented control as
 * Settings, on the tour card, driven by the page's own `setLang` — picking a language applies at
 * once (the tour re-renders in it), keeps the name draft and the step, is keyboard-operable inside
 * the tour (arrows select, Esc still skips, the name field keeps Enter), and shows only there.
 */
import React, { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { useGuide, type GuideApi, type GuideHost } from '../renderer/hooks/home/useGuide'
import { GuideTour } from '../renderer/components/home/GuideTour'
import { capabilities } from '../renderer/lib/guide/capabilities'
import { LangContext, ceviriUygula, type Lang, type LangContextValue } from '../renderer/lib/i18n'

const CTX = { caps: capabilities({ isGitRepo: true }) }

function mockHost() {
  const host = {
    screen: vi.fn(), settings: vi.fn(), 'workspace.tab': vi.fn(),
    'workspace.width': vi.fn(), 'workspace.peek': vi.fn(),
    'preview.open': vi.fn(), drawer: vi.fn(), menu: vi.fn(),
    showGuideScreen: vi.fn(), snapshot: vi.fn(() => 'SNAP'), restore: vi.fn(), unprepare: vi.fn(), focusComposer: vi.fn(),
  }
  return host as unknown as GuideHost
}

let api: GuideApi

/** Renders the tour the way the page does, with a live language state behind `setLang`
 *  (home.tsx's provider: state + localStorage + header; the tour itself only calls `setLang`). */
function Harness({ host, userName = '', startLang = 'en' as Lang, onSetLang }: {
  host: GuideHost; userName?: string; startLang?: Lang; onSetLang?: (l: Lang) => void
}) {
  const g = useGuide({ ctx: CTX, host, userName, saveName: vi.fn() as any, appVersion: '3.2.0', frameReady: false })
  api = g
  const [lang, setLang] = useState<Lang>(startLang)
  const value: LangContextValue = {
    lang,
    setLang: l => { onSetLang?.(l); setLang(l) },
    t: (k, v) => ceviriUygula(lang, k, v),
  }
  return (
    <LangContext.Provider value={value}>
      <div className="app">
        {g.tour && <GuideTour tour={g.tour} approvalMode="balanced" onNext={g.next} onBack={g.back} onSkip={g.skip} onNameDraft={g.setNameDraft} />}
      </div>
    </LangContext.Provider>
  )
}

// The name step renders both choices with their own language's name, as Settings does; the group's
// accessible name is the translated label. Selected = the language in use.
const group = () => screen.getByRole('radiogroup', { name: 'Language' })
const option = (name: string) => screen.getByRole('radio', { name })
const checked = (name: string) => option(name).getAttribute('aria-checked') === 'true'

beforeEach(() => {
  localStorage.clear()
  vi.spyOn(console, 'info').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

describe('the name step\'s language choice', () => {
  it('shows only on the name step: not on later tour steps, not on other topics', () => {
    const host = mockHost()
    render(<Harness host={host} />)
    act(() => api.openTour(1))
    expect(group).toBeTruthy()
    act(() => api.go(2))
    expect(screen.queryByRole('radiogroup')).toBeNull()
    act(() => api.playTopic('ask'))
    expect(screen.queryByRole('radiogroup')).toBeNull()
  })

  it('the option in each own language, the current language selected', () => {
    const host = mockHost()
    const { rerender } = render(<Harness host={host} startLang="en" />)
    act(() => api.openTour(1))
    expect(screen.getByText('Language')).toBeTruthy()
    expect(checked('English')).toBe(true)
    expect(checked('Türkçe')).toBe(false)
    // a fresh page speaking Turkish: the choice follows the language, not the click order
    rerender(<Harness key="tr-page" host={host} startLang="tr" />)
    act(() => api.openTour(1))
    expect(screen.getByText('Dil')).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: 'Dil' })).toBeTruthy()
    expect(checked('Türkçe')).toBe(true)
    expect(checked('English')).toBe(false)
  })

  it('choosing calls the page\'s setLang and the tour speaks the new language at once', () => {
    const host = mockHost()
    const setLangCalled: Lang[] = []
    render(<Harness host={host} onSetLang={l => setLangCalled.push(l)} />)
    act(() => api.openTour(1))
    expect(screen.getByRole('heading', { name: "I'm Gamachine. What should I call you?" })).toBeTruthy()
    expect(screen.getByTestId('tour-next').textContent).toBe('Continue')
    fireEvent.click(option('Türkçe'))
    expect(setLangCalled).toEqual(['tr'])
    expect(screen.getByRole('heading', { name: 'Ben Gamachine. Sana nasıl seslenelim?' })).toBeTruthy()
    expect(screen.getByTestId('tour-next').textContent).toBe('Devam')
    expect(screen.getByText('Dil')).toBeTruthy()
    fireEvent.click(option('English'))
    expect(setLangCalled).toEqual(['tr', 'en'])
    expect(screen.getByTestId('tour-next').textContent).toBe('Continue')
  })

  it('switching keeps the tour on the name step and the typed name', () => {
    const host = mockHost()
    render(<Harness host={host} />)
    act(() => api.openTour(1))
    fireEvent.change(screen.getByTestId('tour-name'), { target: { value: 'Burak' } })
    fireEvent.click(option('Türkçe'))
    expect(screen.getByTestId('guide-tour').getAttribute('data-step')).toBe('1')
    expect((screen.getByTestId('tour-name') as HTMLInputElement).value).toBe('Burak')
    // and the tour continues in Turkish with the name intact
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(screen.getByTestId('guide-tour').getAttribute('data-step')).toBe('2')
    expect(screen.getByText(/^Burak, düz bir cümle yeter/)).toBeTruthy()
  })

  describe('keyboard inside the tour', () => {
    it('arrows select inside the group instead of stepping the tour (Home/End too)', () => {
      const host = mockHost()
      render(<Harness host={host} />)
      act(() => api.openTour(1))
      expect(checked('English')).toBe(true)
      act(() => option('English').focus())
      fireEvent.keyDown(option('English'), { key: 'ArrowRight' })
      expect(document.activeElement).toBe(option('Türkçe'))
      expect(checked('Türkçe')).toBe(true)
      // the tour did not step
      expect(screen.getByTestId('guide-tour').getAttribute('data-step')).toBe('1')
      fireEvent.keyDown(option('Türkçe'), { key: 'Home' })
      expect(checked('English')).toBe(true)
    })

    it('Esc still skips from the choice, and a keydown on the name input leaves the tour\'s keys alone', () => {
      const host = mockHost()
      render(<Harness host={host} />)
      act(() => api.openTour(1))
      act(() => option('English').focus())
      fireEvent.keyDown(option('English'), { key: 'Escape' })
      expect(screen.queryByTestId('guide-tour')).toBeNull()
    })

    it('the options are real buttons, reached by focus (Tab order)', () => {
      const host = mockHost()
      render(<Harness host={host} />)
      act(() => api.openTour(1))
      for (const el of [option('English'), option('Türkçe')]) {
        expect(el.tagName).toBe('BUTTON')
        expect((el as HTMLButtonElement).type).toBe('button')
        act(() => el.focus())
        expect(document.activeElement).toBe(el)
      }
    })
  })
})
