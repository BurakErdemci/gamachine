import { afterEach, describe, expect, it } from 'vitest'
import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { ToolBlock } from '../renderer/components/home/ToolBlock'
import { summarizeTools } from '../renderer/components/home/ToolGroup'
import { userStopped } from '../renderer/lib/computerStep'
import { aktifDilAyarla } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); aktifDilAyarla(null) })

const halted = '{"halted":true,"stop_reason":"halted","steps":[' + ' '.repeat(4000)

describe('userStopped', () => {
  it.each(['computer_run_steps', 'mcp__gamachineComputer__computer_run_steps'])(
    'recognizes an early halt marker in long, truncated output for %s', tool => {
      expect(userStopped(tool, halted)).toBe(true)
    },
  )

  it('allows whitespace around the stop reason', () => {
    expect(userStopped('computer_run_steps', '{"stop_reason" \n : \t "halted"}')).toBe(true)
  })

  it.each([
    ['computer_run_steps', '{"stop_reason":"disabled"}'],
    ['computer_run_steps', '{"stop_reason":"unverified"}'],
    ['computer_run_steps', '{"halted":true}'],
    ['computer_run_steps', undefined],
    ['computer_run_steps', ''],
    ['computer_run_steps', ' '.repeat(400) + '{"stop_reason":"halted"}'],
    ['computer_run_steps', ' '.repeat(390) + '{"stop_reason":"halted"}'],
    ['Bash', halted],
    ['mcp__unityMCP__computer_run_steps', halted],
    ['other_computer_run_steps', halted],
    ['other_mcp__gamachineComputer__run_steps', halted],
  ])('rejects output without an early computer halt marker (%s, %s)', (tool, output) => {
    expect(userStopped(tool, output)).toBe(false)
  })
})

describe('summarizeTools stopped batches', () => {
  it('counts stopped items separately immediately before real failures', () => {
    const parts = summarizeTools([
      { tool: 'computer_run_steps', success: false, output: halted },
      { tool: 'computer_run_steps', success: false, output: '{"failed_step":0}' },
    ])
    expect(parts).toEqual([
      { key: 'tool.sum.computer', values: { sayi: 2 } },
      { key: 'tool.sum.stopped', values: { sayi: 1 } },
      { key: 'tool.sum.failed', values: { sayi: 1 } },
    ])
  })

  it('keeps disabled and unverified batches as failures and ignores successful halt markers', () => {
    const parts = summarizeTools([
      { tool: 'computer_run_steps', success: false, output: '{"stop_reason":"disabled"}' },
      { tool: 'computer_run_steps', success: false, output: '{"stop_reason":"unverified"}' },
      { tool: 'computer_run_steps', success: true, output: halted },
      { tool: 'computer_run_steps', output: halted },
    ])
    expect(parts).toEqual([
      { key: 'tool.sum.computer', values: { sayi: 4 } },
      { key: 'tool.sum.failed', values: { sayi: 2 } },
    ])
  })
})

describe('ToolBlock stopped batches', () => {
  it.each([['tr', 'durduruldu', 'başarısız'], ['en', 'stopped', 'failed']] as const)(
    'renders a stopped label and marker without failure styling in %s', (lang, stopped, failed) => {
      aktifDilAyarla(lang)
      const { container } = render(
        <ToolBlock tool="computer_run_steps" success={false} output={halted} />,
      )
      expect(screen.getByText(stopped).className).toBe('tool-stopped')
      expect(screen.queryByText(failed)).toBeNull()
      expect(container.querySelector('.tool-step')!.hasAttribute('data-stopped')).toBe(true)
      expect(container.querySelector('[data-failed]')).toBeNull()
    },
  )

  it.each(['disabled', 'unverified', 'failed'])(
    'preserves failure labels and styling for %s', reason => {
      aktifDilAyarla('tr')
      const { container } = render(
        <ToolBlock tool="computer_run_steps" success={false} output={`{"stop_reason":"${reason}"}`} />,
      )
      expect(screen.getByText('başarısız').className).toBe('tool-fail')
      expect(screen.queryByText('durduruldu')).toBeNull()
      expect(container.querySelector('.tool-step')!.hasAttribute('data-failed')).toBe(true)
      expect(container.querySelector('[data-stopped]')).toBeNull()
    },
  )

  it('does not mark a successful result as stopped', () => {
    aktifDilAyarla('tr')
    const { container } = render(
      <ToolBlock tool="computer_run_steps" success={true} output={halted} />,
    )
    expect(screen.queryByText('durduruldu')).toBeNull()
    expect(screen.queryByText('başarısız')).toBeNull()
    expect(container.querySelector('[data-stopped], [data-failed]')).toBeNull()
  })
})
