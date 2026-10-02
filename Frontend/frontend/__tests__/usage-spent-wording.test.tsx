/**
 * The usage rows say SPENT, in words. Claude's /usage counts what is used, the Codex app
 * what is left; a bare "%66" beside the Codex app's "34% left" read as the opposite
 * (owner, 2 Oct 2026). The owner chose Claude's direction, so the number stays "used".
 */
import { describe, it, expect, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

import { UseBlock } from '../renderer/components/home/UsageMeters'
import { aktifDilAyarla } from '../renderer/lib/i18n'
import type { UsageFamily } from '../renderer/lib/usageLimits'

afterEach(() => { cleanup(); aktifDilAyarla('tr') })

const NOW = '2026-10-02T12:00:00Z'
const FAM: UsageFamily = {
  family: 'codex', status: 'ok', plan: 'plus', measured_at: NOW, stale: false, error: null,
  windows: [
    { id: '5h', group: null, label: '5h', kind: '5h', used_pct: 66, resets_at: '2026-10-02T15:40:00Z', resets_text: null },
    { id: 'week', group: null, label: 'week', kind: 'week', used_pct: 44, resets_at: '2026-10-06T07:00:00Z', resets_text: null },
  ],
} as UsageFamily

describe('usage rows: spent, spelled out', () => {
  it('tr: "%66 kullanıldı" and "yenilenme <time>", never the remaining 34', () => {
    aktifDilAyarla('tr')
    render(<UseBlock fam={FAM} nowIso={NOW} />)
    const rows = screen.getAllByTestId('use-row')
    expect(rows[0].querySelector('.use-v')?.textContent).toBe('%66 kullanıldı')
    expect(rows[1].querySelector('.use-v')?.textContent).toBe('%44 kullanıldı')
    expect(rows[0].querySelector('.use-r')?.textContent).toMatch(/^yenilenme \d\d:\d\d$/)
    expect(screen.getByTestId('use-block').textContent).not.toContain('34')
  })

  it('en: "66% used" and "resets <time>"', () => {
    aktifDilAyarla('en')
    render(<UseBlock fam={FAM} nowIso={NOW} />)
    const rows = screen.getAllByTestId('use-row')
    expect(rows[0].querySelector('.use-v')?.textContent).toBe('66% used')
    expect(rows[0].querySelector('.use-r')?.textContent).toMatch(/^resets \d\d:\d\d$/)
  })
})
