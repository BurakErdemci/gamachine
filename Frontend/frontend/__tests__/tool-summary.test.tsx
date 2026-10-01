/**
 * The v4 tool chip: one summary line per turn ("read 3 files · wrote ScoreManager.cs"), the steps
 * folded under it, each step still opening its parameters and output.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { ToolGroup, summarizeTools } from '../renderer/components/home/ToolGroup'
import { aktifDilAyarla } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); aktifDilAyarla(null) })

const READS = [
  { tool: 'Read', args: { file_path: 'C:/p/Assets/A.cs' } },
  { tool: 'Read', args: { file_path: 'C:/p/Assets/B.cs' } },
  { tool: 'Grep', args: { pattern: 'x' } },
]

describe('summarizeTools', () => {
  it('counts reads, names a single write, counts the rest and the failures', () => {
    const parts = summarizeTools([
      ...READS,
      { tool: 'Write', args: { file_path: 'C:/p/Assets/Scripts/ScoreManager.cs' } },
      { tool: 'Bash', args: { command: 'ls' }, success: false },
      { tool: 'mcp__unityMCP__manage_scene' },
      { tool: 'TodoWrite' },
    ])
    expect(parts.map(p => p.key)).toEqual([
      'tool.sum.read', 'tool.sum.wrote', 'tool.sum.searched', 'tool.sum.ran', 'tool.sum.unity', 'tool.sum.other', 'tool.sum.failed',
    ])
    expect(parts[0].values).toEqual({ sayi: 2 })
    expect(parts[1].code).toBe('ScoreManager.cs')
    expect(parts.at(-1)!.values).toEqual({ sayi: 1 })
  })

  it('names a single read by file', () => {
    expect(summarizeTools([READS[0]])[0]).toMatchObject({ key: 'tool.sum.readOne', code: 'A.cs' })
  })
})

describe('ToolGroup', () => {
  it('draws one summary line with the steps folded under it', () => {
    aktifDilAyarla('en')
    render(<ToolGroup tools={[...READS, { tool: 'Write', args: { file_path: 'C:/p/S.cs' } }]} />)
    expect(screen.getByTestId('tool-summary').textContent).toBe('read 2 files · wrote S.cs · searched 1 times')
    const details = screen.getByTestId('tool-row').querySelector('details')!
    expect(details.open).toBe(false)
    expect(details.querySelectorAll('.tool-step')).toHaveLength(4)
  })

  it('a step still opens its parameters and output', () => {
    aktifDilAyarla('en')
    render(<ToolGroup tools={[{ tool: 'Bash', args: { command: 'ls -la' }, output: 'total 0' }]} />)
    fireEvent.click(screen.getByText('Bash'))
    expect(screen.getByText(/ls -la/)).toBeTruthy()
    expect(screen.getByText('total 0')).toBeTruthy()
  })

  it('"Details" opens the last written file in the panel', () => {
    aktifDilAyarla('en')
    const open = vi.fn()
    render(<ToolGroup tools={[...READS, { tool: 'Edit', args: { file_path: 'C:/p/Assets/C.cs' } }]} onOpenFile={open} />)
    fireEvent.click(screen.getByText('Details'))
    expect(open).toHaveBeenCalledWith('C:/p/Assets/C.cs')
  })

  it('offers no "Details" when no step names an openable file', () => {
    aktifDilAyarla('en')
    render(<ToolGroup tools={[{ tool: 'Bash', args: { command: 'ls' } }]} onOpenFile={vi.fn()} />)
    expect(screen.queryByText('Details')).toBeNull()
  })
})
