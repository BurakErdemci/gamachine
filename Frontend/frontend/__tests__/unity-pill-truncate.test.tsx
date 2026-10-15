import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'

afterEach(cleanup)

describe('Unity pill in a narrow window', () => {
  const longName = 'MyVeryLongUnityProjectNameThatWouldRunOverTheModelSelector_FinalFinal_v2'

  it('truncates the project name and keeps the full name in a tooltip', () => {
    render(
      <UnityMcpToggle status="connected" toggling={false} reason={null} error={null}
        onToggle={vi.fn()} projectName={longName} />,
    )
    const path = screen.getByText(longName)
    expect(path.className).toContain('truncate')
    expect(path.className).toContain('min-w-0')
    expect(path.className).toContain('max-w-[220px]')
    expect(path.getAttribute('title')).toBe(longName)
  })

  it('lets the pill shrink without overriding its content minimum', () => {
    render(
      <UnityMcpToggle status="connected" toggling={false} reason={null} error={null}
        onToggle={vi.fn()} projectName={longName} />,
    )
    const bay = screen.getByTestId('unity-bay')
    expect(bay.className).toContain('!shrink')
    expect(bay.className).not.toContain('!min-w-0')
  })

  it('defines the topbar container tiers and the bay content minimum', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/shell.css'), 'utf8')
    expect(css).toMatch(/container:\s*topbar\s*\/\s*inline-size/)
    expect(css).toMatch(/@container topbar \(max-width: 620px\)\s*\{/)
    expect(css).toMatch(/@container topbar \(max-width: 440px\)\s*\{/)
    const unityRule = css.match(/\n\.unity\s*\{([^}]*)\}/)?.[1]
    expect(unityRule).toMatch(/min-width:\s*min-content/)
  })
})
