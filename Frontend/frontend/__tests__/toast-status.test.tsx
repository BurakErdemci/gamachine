/**
 * The v4 toast (owner, 3 Oct 2026: the yellow-bordered warnings were left over from the old UI).
 * Each status renders as the same `.gm-toast` with its status as an attribute; the colour comes
 * from a per-theme `--toast-<status>` token, never from a Tailwind colour family.
 */
import { describe, it, expect, afterEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, cleanup } from '@testing-library/react'
import { ToastContainer, type ToastType } from '../renderer/components/ui/Toast'

afterEach(() => cleanup())

const STATUSES: ToastType[] = ['info', 'success', 'warning', 'error']
const TOKEN: Record<ToastType, string> = { info: 'info', success: 'ok', warning: 'warn', error: 'err' }
const css = (f: string) => readFileSync(resolve(__dirname, '../renderer/styles/gm', f), 'utf8')

describe('toast statuses', () => {
  it('renders each status as a gm-toast with its data-status and no raw colour class', () => {
    const toasts = STATUSES.map((type, i) => ({ id: i + 1, message: `m-${type}`, type, duration: 4000 }))
    const { container } = render(<ToastContainer toasts={toasts} onDismiss={() => {}} />)
    const nodes = Array.from(container.querySelectorAll<HTMLElement>('.gm-toast'))
    expect(nodes.map(n => n.dataset.status)).toEqual(STATUSES)
    for (const n of nodes) {
      expect(n.className).toBe('gm-toast')
      expect(n.querySelector('.gm-toast-k')?.textContent).toBeTruthy()
      expect(n.querySelector('.gm-toast-life')).toBeTruthy()
      expect(n.style.getPropertyValue('--toast-life')).toBe('4000ms')
    }
    expect(nodes.find(n => n.dataset.status === 'error')!.getAttribute('role')).toBe('alert')
    expect(nodes.find(n => n.dataset.status === 'info')!.getAttribute('role')).toBe('status')
  })

  it('every status maps to a --toast-<status> tone in toast.css', () => {
    const s = css('toast.css')
    for (const type of STATUSES.filter(t => t !== 'info')) {
      expect(s).toMatch(new RegExp(`\\.gm-toast\\[data-status="${type}"\\]\\s*\\{\\s*--tone: var\\(--toast-${TOKEN[type]}\\)`))
    }
    expect(s).toMatch(/\.gm-toast \{[^}]*--tone: var\(--toast-info\)/)
  })

  it('every theme defines all four toast tones', () => {
    const tokens = css('tokens.css')
    for (const theme of ['arena', 'sade', 'pafta', 'atolye']) {
      const blocks = tokens.split(`:root[data-theme="${theme}"] {`).slice(1).map(b => b.slice(0, b.indexOf('\n}')))
      const all = blocks.join('\n')
      for (const k of Object.values(TOKEN)) expect(all, `${theme} --toast-${k}`).toMatch(new RegExp(`--toast-${k}:`))
    }
  })
})
