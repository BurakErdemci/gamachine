import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { translations } from '../renderer/lib/i18n'
import { ComputerLivePanel, actionKey, formatElapsed } from '../renderer/components/home/ComputerLivePanel'
import { ComputerLiveIndicator } from '../renderer/components/home/ComputerLiveIndicator'
import ComputerLivePage from '../renderer/pages/computer-live'
import { Workspace } from '../renderer/components/home/Workspace'

const tr = translations.tr
const FRAME = 'data:image/png;base64,iVBORw0KGgo='

let listeners: Map<string, (payload: unknown) => void>
let invoke: ReturnType<typeof vi.fn>
let status: Record<string, unknown>
let started: number

function active(over: Record<string, unknown> = {}) {
  return {
    active: true, running: true, conversationId: 7, startedAt: started,
    last: { tool: 'computer_type_text', bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, window_id: 55, at: Date.now(), decision: 'allow' },
    target: { bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, window_id: 55 },
    frame: null, at: Date.now(), ...over,
  }
}

function push(payload: unknown) {
  act(() => { listeners.get('computer-use-live')?.(payload) })
}

beforeEach(() => {
  started = Date.now() - 83_500
  listeners = new Map()
  status = { active: false, running: true, conversationId: null, startedAt: null, last: null, target: null, panelVisible: false }
  invoke = vi.fn(async (channel: string, req: any) => {
    if (channel === 'computer-use-live' && req?.op === 'status') return status
    if (channel === 'computer-use') return { halted: true }
    return { ok: true }
  })
  vi.stubGlobal('ipc', {
    invoke,
    on: vi.fn((channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) }),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.documentElement.classList.remove('cul-window')
})

describe('ComputerLivePanel', () => {
  it('shows the off state when the driver is not running, and reports its visibility to main', async () => {
    status = { ...status, running: false }
    const { unmount, container } = render(<ComputerLivePanel visible />)
    expect(await screen.findByText(tr['cu.live.off'])).toBeTruthy()
    expect(screen.getByText(tr['cu.live.offHint'])).toBeTruthy()
    expect(container.querySelector('.cul-panel')!.getAttribute('data-state')).toBe('off')
    expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'panel-visible', visible: true })
    expect(screen.queryByRole('button', { name: new RegExp(tr['cu.live.stop']) })).toBeNull()
    unmount()
    expect(invoke).toHaveBeenLastCalledWith('computer-use-live', { op: 'panel-visible', visible: false })
  })

  it('shows the idle state while the driver runs but nothing is being driven', async () => {
    const { container } = render(<ComputerLivePanel visible={false} />)
    expect(await screen.findByText(tr['cu.live.idle'])).toBeTruthy()
    expect(container.querySelector('.cul-panel')!.getAttribute('data-state')).toBe('idle')
    expect(container.querySelector('img')).toBeNull()
    expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'panel-visible', visible: false })
  })

  it('tells main when it becomes visible', async () => {
    const { rerender } = render(<ComputerLivePanel visible={false} />)
    rerender(<ComputerLivePanel visible />)
    await waitFor(() => expect(invoke).toHaveBeenLastCalledWith('computer-use-live', { op: 'panel-visible', visible: true }))
  })

  it('draws the live frame, the app, the elapsed time and the last step; keeps the frame on state-only updates', async () => {
    render(<ComputerLivePanel visible />)
    await screen.findByText(tr['cu.live.idle'])
    push(active())
    expect(screen.getByText(tr['cu.live.waiting'])).toBeTruthy()
    push(active({ frame: FRAME }))
    const img = await screen.findByRole('img', { name: 'TextEdit penceresinin canlı görüntüsü' })
    expect(img.getAttribute('src')).toBe(FRAME)
    expect(screen.getByText('TextEdit')).toBeTruthy()
    expect(screen.getByText('01:23')).toBeTruthy()
    expect(screen.getByText(tr['cu.live.act.type'])).toBeTruthy()
    push(active({ frame: null }))
    expect(screen.getByRole('img', { name: /TextEdit/ }).getAttribute('src')).toBe(FRAME)
    push({ ...active(), active: false, frame: null })
    expect(screen.queryByRole('img', { name: /TextEdit/ })).toBeNull()
    expect(screen.getByText(tr['cu.live.idle'])).toBeTruthy()
  })

  it('ignores a frame that is not an image data URL', async () => {
    render(<ComputerLivePanel visible />)
    await screen.findByText(tr['cu.live.idle'])
    push(active({ frame: 'https://example.invalid/x.png' }))
    expect(screen.queryByRole('img', { name: /TextEdit/ })).toBeNull()
  })

  it('Stop calls the halt contract for the conversation, then tells main the run is over', async () => {
    render(<ComputerLivePanel visible />)
    await screen.findByText(tr['cu.live.idle'])
    push(active())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['cu.live.stop']) }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'halted', conversationId: 7 }))
    const haltCall = invoke.mock.calls.findIndex(([channel]) => channel === 'computer-use')
    expect(invoke.mock.calls[haltCall]).toEqual(['computer-use', { op: 'halt', conversationId: 7 }])
    const haltedCall = invoke.mock.calls.findIndex(([, req]) => req?.op === 'halted')
    expect(haltCall).toBeLessThan(haltedCall)
    expect(screen.getByRole('button', { name: new RegExp(tr['cu.live.stopping']) })).toHaveProperty('disabled', true)
  })

  it('a refused halt re-enables Stop and says so', async () => {
    invoke.mockImplementation(async (channel: string, req: any) => {
      if (channel === 'computer-use') return { error: 'nope' }
      if (req?.op === 'status') return status
      return { ok: true }
    })
    render(<ComputerLivePanel visible />)
    await screen.findByText(tr['cu.live.idle'])
    push(active())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['cu.live.stop']) }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', tr['cu.live.stopFailed'])
    expect(invoke).not.toHaveBeenCalledWith('computer-use-live', { op: 'halted', conversationId: 7 })
    expect(screen.getByRole('button', { name: new RegExp(tr['cu.live.stop']) })).toHaveProperty('disabled', false)
  })
})

describe('ComputerLiveIndicator', () => {
  it('shows the calm tag: who, which app, how long, and Stop', async () => {
    status = { ...active(), panelVisible: false }
    const { container } = render(<ComputerLiveIndicator />)
    expect(await screen.findByText(tr['cu.live.using'])).toBeTruthy()
    expect(screen.getByText('TextEdit')).toBeTruthy()
    expect(screen.getByText('01:23')).toBeTruthy()
    expect(container.querySelector('.cul-grip')).toBeTruthy()
    expect(container.querySelector('.cul-dot')).toBeTruthy()
  })

  it('Stop asks main to halt (main holds the conversation); the body opens the live tab', async () => {
    status = { ...active(), panelVisible: false }
    render(<ComputerLiveIndicator />)
    await screen.findByText(tr['cu.live.using'])
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['cu.live.stop']) }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'halt' }))
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['cu.live.using']) }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'open-panel' }))
    expect(invoke).not.toHaveBeenCalledWith('computer-use', expect.anything())
  })

  it('the indicator page renders the tag on a transparent window', async () => {
    status = { ...active(), panelVisible: false }
    render(<ComputerLivePage />)
    expect(await screen.findByText(tr['cu.live.using'])).toBeTruthy()
    expect(document.documentElement.classList.contains('cul-window')).toBe(true)
  })
})

describe('Workspace: the Bilgisayar tab', () => {
  it('is the fifth tab and selects its pane', () => {
    const onTab = vi.fn()
    const panes = { sahne: <p>scene</p>, dosyalar: <p>files</p>, kod: <p>code</p>, onizleme: <p>preview</p>, bilgisayar: <p>computer</p> }
    const { container } = render(<Workspace open tab="bilgisayar" onTab={onTab} width="dar" onWidth={vi.fn()} onClose={vi.fn()} panes={panes} drawer={null} />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(5)
    expect(tabs[4].textContent).toBe(tr['ws.tabComputer'])
    expect(tabs[4].getAttribute('aria-selected')).toBe('true')
    expect(container.querySelector('[data-pane="bilgisayar"]')!.textContent).toBe('computer')
    fireEvent.click(tabs[0])
    expect(onTab).toHaveBeenCalledWith('sahne')
  })
})

describe('helpers', () => {
  it('formats elapsed time and names the last step', () => {
    expect(formatElapsed(0)).toBe('00:00')
    expect(formatElapsed(83_400)).toBe('01:23')
    expect(formatElapsed(3_723_000)).toBe('1:02:03')
    expect(formatElapsed(-5)).toBe('00:00')
    expect(actionKey('computer_click')).toBe('cu.live.act.click')
    expect(actionKey('computer_double_click')).toBe('cu.live.act.click')
    expect(actionKey('computer_hotkey')).toBe('cu.live.act.key')
    expect(actionKey('computer_window_state')).toBe('cu.live.act.look')
    expect(actionKey(null)).toBe('cu.live.act.other')
  })
})
