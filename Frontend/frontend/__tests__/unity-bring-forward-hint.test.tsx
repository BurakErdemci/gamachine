import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { ceviriUygula, LangContext } from '../renderer/lib/i18n'

afterEach(cleanup)
const base = { toggling: false, reason: null, error: null, onToggle: vi.fn(), slowConnect: true }

it.each(['tr', 'en'] as const)('names the workspace editor while connecting in %s', lang => {
  const t = (key: string, values?: Record<string, string | number>) => ceviriUygula(lang, key, values)
  const view = render(<LangContext.Provider value={{ lang, setLang: vi.fn(), t }}>
    <UnityMcpToggle {...base} status="starting" targetProject="BenchmarkTest4" projectName="BenchmarkTest3" />
  </LangContext.Provider>)
  const hint = lang === 'tr'
    ? "Unity'de BenchmarkTest4 penceresini öne getir"
    : 'Bring the BenchmarkTest4 window to the front in Unity'
  expect(screen.getByText(hint)).toBeTruthy()
  view.rerender(<LangContext.Provider value={{ lang, setLang: vi.fn(), t }}>
    <UnityMcpToggle {...base} status="running" targetProject="BenchmarkTest4" />
  </LangContext.Provider>)
  expect(screen.getByText(hint)).toBeTruthy()
})

it.each([undefined, null, ''])('uses the generic hint when the workspace name is %s', targetProject => {
  render(<UnityMcpToggle {...base} status="running" targetProject={targetProject} />)
  expect(screen.getByText("Unity arka planda olabilir: Unity'yi öne getir")).toBeTruthy()
})

it('keeps the generic English hint when the workspace name is unknown', () => {
  const t = (key: string, values?: Record<string, string | number>) => ceviriUygula('en', key, values)
  render(<LangContext.Provider value={{ lang: 'en', setLang: vi.fn(), t }}>
    <UnityMcpToggle {...base} status="running" />
  </LangContext.Provider>)
  expect(screen.getByText('Unity may be in the background: bring Unity to the front')).toBeTruthy()
})
