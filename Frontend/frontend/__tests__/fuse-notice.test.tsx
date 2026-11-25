/**
 * The fuse (4.1.0 design C3/D5) and the helper result card (D4). After ten automatic turns the
 * backend holds the results; the notice's Continue is POST /subagents/continue (SUBAGENTS_API.md),
 * a 409 "nothing_held" means they were already released. A result arrives as the stored system
 * message "[SUB-AGENT RESULT] #id "name" (model): status".
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup, fireEvent, renderHook, act } from '@testing-library/react'

import { FuseNotice } from '../renderer/components/home/MessageNotices'
import { useHelpers } from '../renderer/hooks/home/useHelpers'
import { HelperResultCard, parseHelperResult, isHelperResult, isHelperFuse } from '../renderer/components/home/HelperResultCard'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => aktifDilAyarla('en'))
afterEach(() => { cleanup(); aktifDilAyarla('tr') })

describe('fuse notice', () => {
  it('one sentence, one button; Continue calls the backend once', async () => {
    const onContinue = vi.fn(async () => true)
    render(<FuseNotice onContinue={onContinue} />)
    const notice = screen.getByTestId('fuse-notice')
    expect(notice.textContent).toContain('Paused after 10 automatic turns.')
    expect(notice.textContent).toContain('allows 10 more automatic turns')
    expect(notice.querySelectorAll('button')).toHaveLength(1)
    await act(async () => { fireEvent.click(screen.getByTestId('fuse-continue')) })
    expect(onContinue).toHaveBeenCalledTimes(1)
  })

  it('a failed Continue says so and can be tried again', async () => {
    const onContinue = vi.fn(async () => false)
    render(<FuseNotice onContinue={onContinue} />)
    await act(async () => { fireEvent.click(screen.getByTestId('fuse-continue')) })
    expect(screen.getByTestId('fuse-notice').textContent).toContain('Could not continue; try again.')
    expect((screen.getByTestId('fuse-continue') as HTMLButtonElement).disabled).toBe(false)
  })

  it('continueTurns posts the parent chat and clears the fuse; 409 nothing_held counts as released, another 409 as still running', async () => {
    const http = {
      get: vi.fn(async () => ({ data: { agents: [], fuse: { tripped: true, held: 2, paused: false } } })),
      post: vi.fn(async () => ({ data: { status: 'started', released: 2 } })),
    }
    const { result } = renderHook(() => useHelpers({ API: 'http://x', conversationId: 12, http }))
    await flush()
    expect(result.current.fuse).toMatchObject({ tripped: true, held: 2 })
    http.get.mockResolvedValue({ data: { agents: [], fuse: { tripped: false, held: 0, paused: false } } })
    let ok: string = ''
    await act(async () => { ok = await result.current.continueTurns() })
    expect(ok).toBe('ok')
    expect(http.post).toHaveBeenCalledWith('http://x/subagents/continue', { conversation_id: 12 }, undefined)
    expect(result.current.fuse).toMatchObject({ tripped: false, held: 0 })

    http.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'nothing_held' } } } })
    await act(async () => { ok = await result.current.continueTurns() })
    expect(ok).toBe('ok')
    http.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { message: 'a turn runs', code: 'running' } } } })
    await act(async () => { ok = await result.current.continueTurns() })
    expect(ok).toBe('running')
    http.post.mockRejectedValueOnce({ response: { status: 500 } })
    await act(async () => { ok = await result.current.continueTurns() })
    expect(ok).toBe('failed')
  })

  // Audit F3: after a Stop the backend holds results with paused=true, tripped=false, held>0.
  it('results held after a Stop: "N results are waiting" with Continue', async () => {
    const onContinue = vi.fn(async () => 'ok' as const)
    render(<FuseNotice onContinue={onContinue} tripped={false} held={2} />)
    const notice = screen.getByTestId('fuse-notice')
    expect(notice.dataset.kind).toBe('held')
    expect(notice.textContent).toContain('2 results are waiting · they ride your next message')
    expect(notice.textContent).not.toContain('automatic turns')
    await act(async () => { fireEvent.click(screen.getByTestId('fuse-continue')) })
    expect(onContinue).toHaveBeenCalledTimes(1)
  })

  it('Continue while something still runs says to try when it stops', async () => {
    render(<FuseNotice onContinue={async () => 'running'} tripped={false} held={1} />)
    expect(screen.getByTestId('fuse-notice').textContent).toContain('1 result is waiting')
    await act(async () => { fireEvent.click(screen.getByTestId('fuse-continue')) })
    expect(screen.getByTestId('fuse-hint').textContent).toBe('Something is still running; try again when it stops.')
    expect((screen.getByTestId('fuse-continue') as HTMLButtonElement).disabled).toBe(false)
  })

  it('home shows the notice whenever results are held, tripped or not', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(home).toContain('const fuseShown = (helpers.fuse?.held ?? 0) > 0;')
    expect(home).toContain('tripped: !!helpers.fuse?.tripped, held: helpers.fuse?.held ?? 0')
  })
})

const RESULT = '[SUB-AGENT RESULT] #3 "tester" (gpt-6.2-sol): done\nThis is your sub-agent\'s result, not a message from the user; the user\'s instructions win. / Bu, kullanıcıdan değil alt ajanından gelen sonuç.\n\nDoorTrigger fires once.\nThe boss wakes once.\nNo errors in the console.\nTwo warnings about lightmaps.\nA fifth line.'

describe('helper result card', () => {
  it('parses the backend message and hides the line meant for the model', () => {
    const r = parseHelperResult(RESULT)!
    expect(r).toMatchObject({ agentId: '3', name: 'tester', model: 'gpt-6.2-sol', status: 'done' })
    expect(r.body.startsWith('DoorTrigger fires once.')).toBe(true)
    expect(isHelperResult({ role: 'system', content: RESULT })).toBe(true)
    expect(isHelperResult({ role: 'user', content: RESULT })).toBe(false)
    expect(isHelperFuse({ role: 'system', content: '[SUB-AGENT FUSE] 10 sub-agent results in a row' })).toBe(true)
  })

  it('shows name, model, status, the first lines with Show more, and opens the conversation', () => {
    const open = vi.fn()
    render(<HelperResultCard content={RESULT} startedTurn onOpen={open} />)
    const card = screen.getByTestId('helper-result')
    expect(card.textContent).toContain('tester')
    expect(card.textContent).toContain('Done')
    expect(card.textContent).not.toContain('not a message from the user')
    expect(card.textContent).not.toContain('A fifth line.')
    fireEvent.click(screen.getByText('Show more'))
    expect(card.textContent).toContain('A fifth line.')
    fireEvent.click(screen.getByText('Open conversation'))
    expect(open).toHaveBeenCalledWith('3')
    expect(screen.getByTestId('helper-wake').textContent).toBe('Started on its own· tester’s result arrived')
  })

  it('no "Started on its own" line when the parent did not go on because of it', () => {
    render(<HelperResultCard content={RESULT} />)
    expect(screen.queryByTestId('helper-wake')).toBeNull()
  })
})
