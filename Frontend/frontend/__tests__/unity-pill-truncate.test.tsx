import { describe, it, expect, vi, afterEach } from 'vitest'
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

  it('lets the pill shrink so it never overlaps its neighbours', () => {
    render(
      <UnityMcpToggle status="connected" toggling={false} reason={null} error={null}
        onToggle={vi.fn()} projectName={longName} />,
    )
    const bay = screen.getByTestId('unity-bay')
    expect(bay.className).toContain('!shrink')
    expect(bay.className).toContain('!min-w-0')
  })
})
