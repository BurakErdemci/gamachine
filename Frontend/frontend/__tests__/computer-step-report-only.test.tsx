import { afterEach, describe, expect, it } from 'vitest'
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { describeComputerOutput } from '../renderer/lib/computerStep'
import { ToolBlock } from '../renderer/components/home/ToolBlock'
import { aktifDilAyarla } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); aktifDilAyarla(null) })

const nonReports = [
  'Action rejected. Diagnostic example: {"executed":1,"total":1,"stop_reason":"halted"}',
  '{"error": {"executed": 1, "total": 1, "stop_reason": "halted", "message": "rejected',
]

const truncatedReport = '{"executed": 3, "total": 40, "failed_step": null, "ok": false, "steps": [{"index": 0, "ok": tr'
const truncatedHaltedReport = '{"halted": true, "stop_reason": "halted", "halt_reason": "Stopped by user", "executed": 3, "total": 40, "steps": [{"index": 0, "ok": tr'

describe('computer batch report recognition', () => {
  it.each(nonReports)('rejects diagnostic text and nested report fields: %s', output => {
    expect(describeComputerOutput(output)).toBeNull()
  })

  it.each([
    [truncatedReport, false],
    [truncatedHaltedReport, true],
    [' \n\t' + truncatedReport.replace('{', '{ \n\t'), false],
    [' \n\t' + truncatedHaltedReport.replace('{', '{ \n\t'), true],
  ] as const)('recovers a real truncated report: %s', (output, stopped) => {
    expect(describeComputerOutput(output)).toMatchObject({
      executed: 3, total: 40, stopped, steps: [],
    })
  })

  it('preserves full JSON reports with other leading fields', () => {
    expect(describeComputerOutput('{"ok":true,"total":1,"executed":1,"steps":[]}'))
      .toMatchObject({ executed: 1, total: 1, stopped: false, steps: [] })
  })
})

describe('failed computer batch diagnostic output', () => {
  it.each(nonReports)('shows the actual output above collapsed raw data: %s', output => {
    aktifDilAyarla('en')
    const { container } = render(<ToolBlock tool="computer_run_steps" success={false} output={output} />)
    fireEvent.click(screen.getByRole('button'))

    expect(container.querySelector('.tool-step')?.getAttribute('data-failed')).toBe('true')
    expect(container.querySelector('.tool-cu-result')).toBeNull()
    const visibleOutput = container.querySelector('.tool-out > div > pre')!
    expect(visibleOutput).not.toBeNull()
    expect(visibleOutput.textContent).toBe(output)
    expect(visibleOutput.closest('details')).toBeNull()
    const raw = screen.getByText('Raw data').closest('details')!
    expect(raw.open).toBe(false)
    expect(raw.querySelector('pre')?.textContent).toBe(output)
  })
})
