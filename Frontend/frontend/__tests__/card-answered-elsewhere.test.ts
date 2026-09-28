/**
 * First answer wins (Backend/app/agentic/cards.py). When a phone, another
 * window or a timeout closed a card first, the desktop's answer gets
 * `{status: "already_answered", by, at, decision}`. That is a known outcome,
 * not an unknown one: the card closes with a note saying who decided, instead
 * of the "sent but unreadable, check the state" warning it used to get.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn(async () => ({ data: [] }))
  return { default: { post, get, delete: vi.fn(), put: vi.fn() }, post, get }
})

import { gateFailure, decisionToast } from '../renderer/hooks/home/gateResponse'
import { useChat } from '../renderer/hooks/home/useChat'
import { aktifDilAyarla, ceviriUygula } from '../renderer/lib/i18n'

const answered = (by: string, decision: string) => ({
  httpOk: true, httpStatus: 200,
  body: { status: 'already_answered', card_id: 'g1', by, at: '2026-09-28T10:00:00Z', decision, outcome: 'x' },
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); aktifDilAyarla('tr') })

describe('gateFailure · already_answered', () => {
  it('a phone approval is a clear info note naming the phone, not an uncertain warning', () => {
    aktifDilAyarla('tr')
    const f = gateFailure('command', answered('phone:iPhone 17', 'approve'))!
    expect(f.message).toBe('Telefondan (iPhone 17) onaylandı.')
    expect(f.type).toBe('info')
    expect(f.uncertain).toBeUndefined()
    expect(f.answeredElsewhere).toEqual({ by: 'phone:iPhone 17', at: '2026-09-28T10:00:00Z', decision: 'approve' })
  })

  it('reject and question answers from the phone, in both languages', () => {
    aktifDilAyarla('tr')
    expect(gateFailure('mcp', answered('phone:Pixel', 'reject'))!.message).toBe('Telefondan (Pixel) reddedildi.')
    expect(gateFailure('question', answered('phone:Pixel', 'answer'))!.message).toBe('Telefondan (Pixel) cevaplandı.')
    aktifDilAyarla('en')
    expect(gateFailure('mcp', answered('phone:Pixel', 'reject'))!.message).toBe('Rejected from the phone (Pixel).')
    expect(ceviriUygula('en', 'gate.answered.phoneApproved', { cihaz: 'A' })).toBe('Approved from the phone (A).')
  })

  it('another window and a system close (timeout / Stop) get their own notes', () => {
    aktifDilAyarla('tr')
    const other = gateFailure('command', answered('desktop', 'reject'))!
    expect(other.message).toBe('Bu kart başka bir pencereden zaten reddedilmişti.')
    expect(other.type).toBe('info')
    const closed = gateFailure('command', answered('system', 'reject'))!
    expect(closed.message).toContain('İşlem yapılmadı')
    expect(closed.type).toBe('warning')
    expect(closed.uncertain).toBeUndefined()
  })

  it('decisionToast passes the note through unchanged', () => {
    aktifDilAyarla('tr')
    expect(decisionToast(gateFailure('mcp', answered('phone:X', 'approve')), 'sent'))
      .toEqual({ message: 'Telefondan (X) onaylandı.', type: 'info' })
  })
})

describe('command card answered on the phone first', () => {
  it('the desktop answer closes the card and shows who decided', async () => {
    aktifDilAyarla('tr')
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/command-approval/')) {
        return { ok: true, status: 200, json: async () => answered('phone:iPhone', 'reject').body }
      }
      return { ok: true, status: 200, body: { getReader: () => ({ read: () => new Promise(() => {}) }) }, json: async () => ({}) }
    }))
    const showToast = vi.fn()
    const { result } = renderHook(() => useChat('http://127.0.0.1:8000', { id: 1, name: 'b', sessionToken: 't' } as any,
      { provider_type: 'subscription', model_name: 'claude-opus-5' } as any, '/ws', showToast, vi.fn(), (n: string) => n))
    act(() => { result.current.setPendingCommand({ gateId: 'g1', command: 'rm -r x', messageId: 1 } as any) })
    let failure: any
    await act(async () => { failure = await result.current.approveCommand('g1', true) })
    expect(showToast).toHaveBeenCalledWith('Telefondan (iPhone) reddedildi.', 'info')
    expect(failure.answeredElsewhere.by).toBe('phone:iPhone')
    expect(result.current.pendingCommand).toBeNull()
  })
})
