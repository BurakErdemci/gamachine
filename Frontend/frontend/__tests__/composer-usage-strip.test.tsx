/**
 * The composer strip's plan usage ("Kota", owner request 3 Oct 2026): the chat's family shows
 * its 5 h and week numbers next to Hafıza; a family without numbers adds nothing. The key hint
 * left the strip (it was cut off) and now lives on the send button, where the guide points.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: 'idle', elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import { ControlPanel } from '../renderer/components/home/ControlPanel'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { resetLabel, type UsageFamily, type UsageLimits } from '../renderer/lib/usageLimits'
import { KNOWN_ANCHORS, findAnchor } from '../renderer/lib/guide/anchors'
import { topicById } from '../renderer/lib/guide/registry'

const NOW = '2026-10-03T12:00:00Z'
const win = (id: string, kind: '5h' | 'week', used_pct: number) => ({
  id, group: null, label: id, kind, used_pct, resets_text: null,
  resets_at: kind === '5h' ? '2026-10-03T15:40:00Z' : '2026-10-06T07:00:00Z',
})
const fam = (family: UsageFamily['family'], over: Partial<UsageFamily> = {}): UsageFamily => ({
  family, status: 'ok', plan: 'Max', measured_at: NOW, stale: false, error: null,
  windows: [win('five_hour', '5h', 90), win('week', 'week', 27)],
  ...over,
})
const limits = (...families: UsageFamily[]): UsageLimits => ({ now: NOW, families })

const base = {
  thinkingLevel: 'auto' as any, setThinkingLevel: () => {},
  isAnalyzingProject: false, analyzeProject: async () => {},
  exportMemory: async () => {}, importMemory: async () => {},
  compactConversation: async () => {}, isCompacting: false,
  activeConvId: 7,
  contextUsage: { percent: 5, should_compact: false, message_count: 2, estimated: true },
}
const strip = (extra: Record<string, any>) =>
  render(<ControlPanel {...(base as any)} {...extra} />)

beforeEach(() => aktifDilAyarla('tr'))
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Kota group', () => {
  it('shows both percentages for a family with numbers', () => {
    strip({ usage: limits(fam('claude')), usageFamily: 'claude', modelId: 'claude-opus-5' })
    const g = screen.getByTestId('strip-usage')
    expect(g.getAttribute('data-guide')).toBe('strip-usage')
    expect(g.textContent).toContain('Kota')
    expect(g.querySelector('.strip-use-5h b')?.textContent).toBe('%90')
    expect(g.querySelector('.strip-use-week b')?.textContent).toBe('%27')
    // the reset times the menu rows show, in the reader's locale
    const title = g.getAttribute('title') || ''
    expect(title).toContain(resetLabel(win('five_hour', '5h', 90), 'tr', NOW))
    expect(title).toContain(resetLabel(win('week', 'week', 27), 'tr', NOW))
  })

  it('English writes the percent after the number', () => {
    aktifDilAyarla('en')
    strip({ usage: limits(fam('codex')), usageFamily: 'codex', modelId: 'gpt-6' })
    const g = screen.getByTestId('strip-usage')
    expect(g.querySelector('.strip-use-5h b')?.textContent).toBe('90%')
    expect(g.textContent).toContain('Quota')
  })

  it('is hidden for a family without numbers, while loading and with no family', () => {
    const cases: Record<string, any>[] = [
      { usage: limits(fam('agy', { status: 'unavailable', windows: [] })), usageFamily: 'agy' },
      { usage: limits(fam('claude', { status: 'loading', windows: [] })), usageFamily: 'claude' },
      { usage: null, usageFamily: 'claude' },
      { usage: limits(fam('claude')), usageFamily: null }, // API-key provider
    ]
    for (const c of cases) {
      const { container } = strip(c)
      expect(screen.queryByTestId('strip-usage')).toBeNull()
      // nothing reserved: not even its separator
      expect(container.querySelector('.strip-sep-use')).toBeNull()
      cleanup()
    }
  })

  it('marks >= 80 % hot, below it not', () => {
    strip({ usage: limits(fam('claude')), usageFamily: 'claude' })
    const g = screen.getByTestId('strip-usage')
    expect(g.querySelector('.strip-use-5h')?.hasAttribute('data-hot')).toBe(true)
    expect(g.querySelector('.strip-use-5h .energy')?.classList.contains('is-hot')).toBe(true)
    expect(g.querySelector('.strip-use-week')?.hasAttribute('data-hot')).toBe(false)
    expect(g.querySelector('.strip-use-week .energy')?.classList.contains('is-hot')).toBe(false)
  })

  it('the memory value is short; its meaning stays in the title', () => {
    strip({})
    expect(screen.getByTestId('context-percent').textContent).toBe('~%5')
    expect(screen.getByTestId('context-gauge').textContent).not.toContain('dolu')
    expect(screen.getByTestId('context-gauge').getAttribute('aria-label')).toMatch(/doluluk/)
  })
})

describe('key hint', () => {
  it('is no longer in the strip', () => {
    strip({ usage: limits(fam('claude')), usageFamily: 'claude' })
    const s = screen.getByTestId('composer-strip')
    expect(s.textContent).not.toContain('Enter')
    expect(s.querySelector('[data-guide="strip-hint"]')).toBeNull()
  })

  it('is the send button title, and the guide step resolves to that button', () => {
    const { container } = render(
      <AnimatedChatInput value="merhaba" setValue={() => {}} onSendMessage={() => {}} isLoading={false} api="http://127.0.0.1:1" />,
    )
    const send = container.querySelector('[data-send-button]') as HTMLElement
    expect(send.getAttribute('title')).toBe(cevir('composer.sendTitle'))
    expect(send.getAttribute('title')).toContain('Shift+Enter')

    const step = topicById('shortcuts')!.steps[0]
    expect(step.anchor).toBe('composer-send')
    expect((KNOWN_ANCHORS as readonly string[]).includes(step.anchor!)).toBe(true)
    // jsdom lays nothing out; give elements a box so findAnchor sees them as shown
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { x: 0, y: 0, top: 0, left: 0, right: 32, bottom: 32, width: 32, height: 32, toJSON: () => ({}) } as DOMRect,
    )
    expect(findAnchor(step.anchor!, container)).toBe(send)
  })

  it('the anchor survives a running turn with an empty box (the stop button carries it)', () => {
    const { container } = render(
      <AnimatedChatInput value="" setValue={() => {}} onSendMessage={() => {}} isLoading api="http://127.0.0.1:1" />,
    )
    expect(container.querySelector('[data-send-button]')).toBeNull()
    expect(container.querySelector('[data-guide="composer-send"]')).toBe(container.querySelector('[data-stop-button]'))
  })
})
