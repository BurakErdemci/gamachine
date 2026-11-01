import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { unityMcpDialogOku } from '../renderer/hooks/home/useAIConfig'
import { ceviriUygula, LangContext } from '../renderer/lib/i18n'

afterEach(cleanup)
const base = { toggling: false, reason: null, error: null, onToggle: vi.fn(), slowConnect: true, targetProject: 'Game' }
const dialog = { title: 'URP Material upgrade', buttons: ['Proceed', 'Cancel'] }

it.each(['tr', 'en'] as const)('names the blocking Unity dialog instead of asking to bring the editor forward in %s', lang => {
  const t = (key: string, values?: Record<string, string | number>) => ceviriUygula(lang, key, values)
  const hint = lang === 'tr'
    ? "Unity'de “URP Material upgrade” penceresi açık; kapatınca bağlanır."
    : 'A “URP Material upgrade” dialog is open in Unity; it will connect once you close it.'
  const forward = lang === 'tr' ? "Unity'de Game penceresini öne getir" : 'Bring the Game window to the front in Unity'
  const wrap = (node: React.ReactNode) => <LangContext.Provider value={{ lang, setLang: vi.fn(), t }}>{node}</LangContext.Provider>
  const view = render(wrap(<UnityMcpToggle {...base} status="running" dialog={dialog} />))
  expect(screen.getByText(hint)).toBeTruthy()
  expect(screen.queryByText(forward)).toBeNull()
  view.rerender(wrap(<UnityMcpToggle {...base} status="starting" slowConnect={false} dialog={dialog} />))
  expect(screen.getByText(hint)).toBeTruthy()
  view.rerender(wrap(<UnityMcpToggle {...base} status="running" dialog={null} />))
  expect(screen.queryByText(hint)).toBeNull()
  expect(screen.getByText(forward)).toBeTruthy()
})

it.each(['connected', 'off'] as const)('does not show the dialog hint when the status is %s', status => {
  render(<UnityMcpToggle {...base} status={status} dialog={dialog} />)
  expect(screen.queryByText(/URP Material upgrade/)).toBeNull()
})

it('reads only a well-formed dialog from the status response', () => {
  expect(unityMcpDialogOku({ title: 'T', buttons: ['OK', 3] })).toEqual({ title: 'T', buttons: ['OK'] })
  expect(unityMcpDialogOku({ title: 'T' })).toEqual({ title: 'T', buttons: [] })
  for (const bad of [undefined, null, 'T', {}, { title: '' }, { title: 7 }]) expect(unityMcpDialogOku(bad)).toBeNull()
})
