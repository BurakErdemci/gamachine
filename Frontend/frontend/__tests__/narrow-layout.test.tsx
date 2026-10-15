/**
 * Narrow window: below 1100 px the sidebar and the workspace panel take turns beside the chat
 * (lib/workspacePanel.ts useOneSideColumnWhenNarrow); at 1100 px and wider both stay as they are.
 * jsdom has no layout, so the media query is mocked (with change events, to resize the window)
 * and the CSS is checked as text.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React, { useState } from 'react'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'

import { useNarrowWindow, useOneSideColumnWhenNarrow, WS_NARROW_QUERY } from '../renderer/lib/workspacePanel'

/** The wiring home.tsx does, with two buttons standing in for the sidebar and panel toggles. */
const Harness: React.FC = () => {
  const [side, setSide] = useState(true)
  const [panel, setPanel] = useState(true)
  useOneSideColumnWhenNarrow(useNarrowWindow(), { open: side, setOpen: setSide }, { open: panel, setOpen: setPanel })
  return (
    <>
      <button data-testid="side" data-open={String(side)} onClick={() => setSide(!side)} />
      <button data-testid="panel" data-open={String(panel)} onClick={() => setPanel(!panel)} />
    </>
  )
}

/** A window whose width can change: matchMedia answers max-width queries and fires `change`. */
const stubWindow = (initial: number) => {
  let width = initial
  const lists: Array<{ max: number; mq: any; listeners: Set<() => void> }> = []
  vi.stubGlobal('matchMedia', vi.fn((query: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(query)?.[1] ?? Infinity)
    const listeners = new Set<() => void>()
    const mq = {
      get matches() { return width <= max },
      media: query,
      addEventListener: (_: string, f: () => void) => listeners.add(f),
      removeEventListener: (_: string, f: () => void) => listeners.delete(f),
    }
    lists.push({ max, mq, listeners })
    return mq
  }))
  return {
    resize(next: number) {
      act(() => {
        const before = lists.map(l => width <= l.max)
        width = next
        lists.forEach((l, i) => { if (before[i] !== (width <= l.max)) l.listeners.forEach(f => f()) })
      })
    },
  }
}

const state = () => ({ side: screen.getByTestId('side').dataset.open, panel: screen.getByTestId('panel').dataset.open })

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('sidebar and panel in a narrow window', () => {
  it('starts with the sidebar and the panel folded away below 1100 px', () => {
    stubWindow(960)
    render(<Harness />)
    expect(state()).toEqual({ side: 'true', panel: 'false' })
  })

  it('keeps both open at 1100 px and wider', () => {
    stubWindow(1100)
    render(<Harness />)
    expect(state()).toEqual({ side: 'true', panel: 'true' })
  })

  it('treats a window without matchMedia as wide', () => {
    vi.stubGlobal('matchMedia', undefined)
    render(<Harness />)
    expect(state()).toEqual({ side: 'true', panel: 'true' })
  })

  it('folds the sidebar away when the panel opens, and brings it back when the panel closes', () => {
    stubWindow(960)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('panel'))
    expect(state()).toEqual({ side: 'false', panel: 'true' })
    fireEvent.click(screen.getByTestId('panel'))
    expect(state()).toEqual({ side: 'true', panel: 'false' })
  })

  it('closes the panel when the sidebar opens', () => {
    stubWindow(960)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('panel'))
    fireEvent.click(screen.getByTestId('side'))
    expect(state()).toEqual({ side: 'true', panel: 'false' })
  })

  it('leaves a sidebar the user closed closed when the panel closes', () => {
    stubWindow(960)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('side'))
    fireEvent.click(screen.getByTestId('panel'))
    expect(state()).toEqual({ side: 'false', panel: 'true' })
    fireEvent.click(screen.getByTestId('panel'))
    expect(state()).toEqual({ side: 'false', panel: 'false' })
  })

  it('folds the panel when the window narrows and brings it back when it widens', () => {
    const win = stubWindow(1400)
    render(<Harness />)
    win.resize(960)
    expect(state()).toEqual({ side: 'true', panel: 'false' })
    win.resize(1400)
    expect(state()).toEqual({ side: 'true', panel: 'true' })
  })

  it('brings the folded sidebar back when the window widens with the panel open', () => {
    const win = stubWindow(960)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('panel'))
    win.resize(1400)
    expect(state()).toEqual({ side: 'true', panel: 'true' })
  })

  it('does not reopen a panel the user closed in the narrow window', () => {
    const win = stubWindow(1400)
    render(<Harness />)
    win.resize(960)
    fireEvent.click(screen.getByTestId('panel'))   // open (sidebar folds)
    fireEvent.click(screen.getByTestId('panel'))   // close (sidebar returns)
    win.resize(1400)
    expect(state()).toEqual({ side: 'true', panel: 'false' })
  })

  it('changes nothing in a wide window', () => {
    stubWindow(1400)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('side'))
    fireEvent.click(screen.getByTestId('side'))
    expect(state()).toEqual({ side: 'true', panel: 'true' })
  })
})

describe('narrow-layout css', () => {
  const css = (f: string) => fs.readFileSync(path.join(__dirname, '..', 'renderer', 'styles', 'gm', f), 'utf-8')

  it('keeps the sidebar handoff at 1100px and overlays only when the main area cannot fit both columns', () => {
    expect(WS_NARROW_QUERY).toBe('(max-width: 1099px)')
    expect(css('workspace.css')).not.toContain('@media (max-width: 1099px)')
    expect(css('workspace.css')).toContain('@container app-body (width < 720px)')
    expect(css('workspace.css')).toMatch(/\.app-right\s*\{[^}]*position:\s*absolute/)
  })

  it('starts a greeting taller than the chat at the top instead of cutting it', () => {
    expect(css('thread.css')).toMatch(/\.empty \{[^}]*justify-content: safe center/)
  })

  it('wraps the greeting at word boundaries and caps the task board at two columns', () => {
    const thread = css('thread.css')
    expect(thread).toMatch(/\.empty-say \{[^}]*overflow-wrap: break-word/)
    expect(thread).toMatch(/\.quests \{[^}]*auto-fit, minmax\(min\(100%, max\(220px, calc\(50% - 6px\)\)\), 1fr\)/)
    for (const f of ['theme-arena', 'theme-atolye', 'theme-ozel', 'theme-pafta']) {
      expect(css(`${f}.thread.css`)).toContain('minmax(0, 1fr)')
    }
  })

  it('lets the Unity hint truncate inside its bay', () => {
    expect(css('shell.css')).toMatch(/\.unity-hint \{[^}]*text-overflow: ellipsis/)
  })
})
