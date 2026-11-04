/**
 * The static export prerenders /home on the server: a useSyncExternalStore without a server
 * snapshot throws there (React error #407) and broke the desktop build of 4 Nov 2026, while
 * every jsdom test passed.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { renderToString } from 'react-dom/server'
import { useBgTasks } from '../renderer/hooks/home/useChat'

function Probe() {
  const tasks = useBgTasks(7)
  return <span>{tasks.length}</span>
}

describe('useBgTasks on the server', () => {
  it('renders without a browser store', () => {
    expect(renderToString(<Probe />)).toContain('0')
  })
})
