import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'

vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))
const confirm = confirmDialog as unknown as ReturnType<typeof vi.fn>

const entry = (host: string, visits = 1) => ({ url: `https://${host}/`, title: host, favicon: '', lastVisit: 1_000, visits })
const props = () => ({ running: false, onStop: vi.fn(async () => {}), onNeedChat: vi.fn(async () => 9) }) as any
const pickable = () => [...document.querySelectorAll<HTMLButtonElement>('button.nt-site')].map(b => b.title)

let invoke: ReturnType<typeof vi.fn>
function stub(fn: (req: any) => unknown) {
  invoke = vi.fn(async (_c: string, req: any) => fn(req))
  vi.stubGlobal('ipc', { invoke, on: vi.fn(() => () => {}) })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); confirm.mockReset() })

describe('empty browser tab after a deletion in settings', () => {
  it('drops the deleted site once the settings page reports a successful delete', async () => {
    let entries = [entry('a.example', 3), entry('b.example', 2)]
    stub((req) => {
      switch (req.op) {
        case 'panel-status': return { active: false }
        case 'history': return { entries }
        case 'history-remove': entries = entries.filter(e => e.url !== req.url); return { ok: true }
        case 'sites': return { sites: [] }
        case 'settings': return { enabled: true, running: false }
        default: return { ok: true }
      }
    })
    render(<><BrowserPanel visible conversationId={3} {...props()} /><BrowserUsePage saved={vi.fn()} /></>)
    await waitFor(() => expect(pickable()).toEqual(['https://a.example/', 'https://b.example/']))
    fireEvent.click((await screen.findAllByTestId('bp-hist-remove'))[0])
    await waitFor(() => expect(pickable()).toEqual(['https://b.example/']))
  })

  it('does not dispatch the event when the delete fails', async () => {
    stub((req) => {
      switch (req.op) {
        case 'history': return { entries: [entry('a.example')] }
        case 'history-remove': return { error: 'failed' }
        case 'sites': return { sites: [] }
        case 'settings': return { enabled: true, running: false }
        default: return { ok: true }
      }
    })
    const seen = vi.fn()
    window.addEventListener('gm-browser-history-changed', seen)
    render(<BrowserUsePage saved={vi.fn()} />)
    fireEvent.click((await screen.findAllByTestId('bp-hist-remove'))[0])
    await waitFor(() => expect(invoke.mock.calls.some(c => c[1]?.op === 'history-remove')).toBe(true))
    await act(async () => { await Promise.resolve() })
    window.removeEventListener('gm-browser-history-changed', seen)
    expect(seen).not.toHaveBeenCalled()
  })

  it('ignores an older history response that arrives after a newer one', async () => {
    const pending: Array<(v: unknown) => void> = []
    stub((req) => {
      if (req.op === 'panel-status') return { active: false }
      if (req.op === 'history') return new Promise(res => { pending.push(res) })
      return { ok: true }
    })
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(pending).toHaveLength(1))
    act(() => { window.dispatchEvent(new Event('gm-browser-history-changed')) })
    await waitFor(() => expect(pending).toHaveLength(2))
    await act(async () => { pending[1]({ entries: [entry('new.example')] }) })
    await waitFor(() => expect(pickable()).toEqual(['https://new.example/']))
    await act(async () => { pending[0]({ entries: [entry('old.example')] }) })
    expect(pickable()).toEqual(['https://new.example/'])
  })
})
