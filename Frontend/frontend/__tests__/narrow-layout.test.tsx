/**
 * Narrow window: below 1100 px the open workspace panel is an overlay drawer over the chat and
 * Escape closes it; at 1100 px and wider nothing changes. jsdom has no layout, so the media query
 * is mocked and the CSS is checked as text.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React, { useState } from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

import { useNarrowWindow, WS_DRAWER_QUERY } from '../renderer/lib/workspacePanel'
import { Workspace } from '../renderer/components/home/Workspace'

const panes = { sahne: <p>scene</p>, dosyalar: <p>files</p>, kod: <p>code</p>, onizleme: <p>preview</p> }

/** The wiring home.tsx does: the hook feeds the panel's `overlay`, the panel's close shuts it. */
const Harness: React.FC = () => {
  const overlay = useNarrowWindow()
  const [open, setOpen] = useState(true)
  return <Workspace open={open} tab="sahne" onTab={vi.fn()} width="dar" onWidth={vi.fn()} onClose={() => setOpen(false)} panes={panes} drawer={null} overlay={overlay} />
}

/** A window of `width` px: matchMedia answers the max-width query the way a browser would. */
const stubWindow = (width: number) => {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => {
    const max = /max-width:\s*(\d+)px/.exec(query)
    return { matches: max ? width <= Number(max[1]) : false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }
  }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('workspace drawer in a narrow window', () => {
  it('is an overlay drawer below 1100 px', () => {
    stubWindow(960)
    render(<Harness />)
    const ws = screen.getByTestId('workspace')
    expect(ws.getAttribute('data-overlay')).toBe('true')
    expect(ws.classList.contains('is-drawer')).toBe(true)
  })

  it('stays a column at 1100 px and wider', () => {
    stubWindow(1100)
    render(<Harness />)
    const ws = screen.getByTestId('workspace')
    expect(ws.getAttribute('data-overlay')).toBeNull()
    expect(ws.classList.contains('is-drawer')).toBe(false)
  })

  it('closes on Escape when it is a drawer', () => {
    stubWindow(960)
    render(<Harness />)
    expect(screen.getByTestId('workspace').hasAttribute('hidden')).toBe(false)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByTestId('workspace').hasAttribute('hidden')).toBe(true)
  })

  it('does not close on Escape when it is a column', () => {
    stubWindow(1400)
    render(<Harness />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByTestId('workspace').hasAttribute('hidden')).toBe(false)
  })

  it('treats a window without matchMedia as wide', () => {
    vi.stubGlobal('matchMedia', undefined)
    render(<Harness />)
    expect(screen.getByTestId('workspace').getAttribute('data-overlay')).toBeNull()
  })
})

describe('narrow-layout css', () => {
  const css = (f: string) => fs.readFileSync(path.join(__dirname, '..', 'renderer', 'styles', 'gm', f), 'utf-8')

  it('uses the same 1100 px breakpoint as the hook', () => {
    expect(WS_DRAWER_QUERY).toBe('(max-width: 1099px)')
    expect(css('workspace.css')).toContain('@media (max-width: 1099px)')
  })

  it('lets the task cards wrap and the greeting shrink in every theme', () => {
    expect(css('thread.css')).toMatch(/\.quests \{[^}]*auto-fit/)
    for (const f of ['theme-arena', 'theme-atolye', 'theme-ozel', 'theme-pafta']) {
      expect(css(`${f}.thread.css`)).toContain('minmax(0, 1fr)')
    }
  })
})
