import { afterEach, describe, expect, it } from 'vitest'
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { describeComputerArgs, describeComputerOutput, unwrapComputerArgs, userStopped } from '../renderer/lib/computerStep'
import { ToolBlock } from '../renderer/components/home/ToolBlock'
import { ToolGroup, summarizeTools } from '../renderer/components/home/ToolGroup'
import { aktifDilAyarla } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); aktifDilAyarla(null) })

const tool = 'mcp__gamachineComputer__computer_run_steps'
const wrapped = { args: { pid: 28425, window_id: 122, delay_ms: 1000, steps: Array.from({ length: 40 }, () => ({ click: { x: 1, y: 2 } })) } }
const stopped = '{"halted": true, "stop_reason": "halted", "halt_reason": "Stopped by user", "executed": 9, "total": 40, "steps": []}'

describe('computer argument fixes', () => {
  it('unwraps only a single own args key containing a plain object', () => {
    expect(unwrapComputerArgs(wrapped)).toBe(wrapped.args)
    const unchanged = [null, [], { args: [] }, { args: 'text' }, { args: {}, pid: 1 }, { args: new Date(0) }]
    for (const args of unchanged) expect(unwrapComputerArgs(args)).toBe(args)
    const nullPrototype = Object.assign(Object.create(null), { args: Object.create(null) })
    expect(unwrapComputerArgs(nullPrototype)).toBe(nullPrototype.args)
    const symbolKey = { args: {}, [Symbol('extra')]: true }
    expect(unwrapComputerArgs(symbolKey)).toBe(symbolKey)
  })

  it('reads wrapped steps and metadata', () => {
    const result = describeComputerArgs(tool, wrapped)!
    expect(result.meta).toEqual(['pid: 28425', 'window_id: 122', 'delay_ms: 1000'])
    expect(result.steps).toHaveLength(40)
    expect(result.steps[0]).toBe('click → (1, 2)')
  })

  it.each(['{"toString":0}', '{"valueOf":0}', '[[1,2],[{"toString":0}]]', '{"__proto__":{"toString":0}}'])(
    'formats hostile JSON values safely and compactly: %s', json => {
      const value = JSON.parse(json)
      const args = { extra: value, steps: [value, { expect: value }, { click: { role: value, name: value, nth: value } },
        { drag: { x: value, y: value } }, { press_key: { key: value, modifiers: [value] } },
        { hotkey: { keys: [value] } }, { type_text: { text: value } }] }
      expect(() => describeComputerArgs(tool, args)).not.toThrow()
      const result = describeComputerArgs(tool, args)!
      expect(result.meta).toEqual([`extra: ${JSON.stringify(value)}`])
      expect(result.steps[1]).toBe(`expect ${JSON.stringify(value)}`)
      expect(result.steps.join(' ')).not.toContain('[object Object]')
      expect(result.steps[2]).toContain(JSON.stringify(value))
      expect(result.steps[3]).toBe(`drag → (${JSON.stringify(value)}, ${JSON.stringify(value)})`)
      expect(result.steps[4]).toBe(`press_key ${JSON.stringify(value)}+${JSON.stringify(value)}`)
      expect(result.steps[5]).toBe(`hotkey ${JSON.stringify(value)}`)
      expect(result.steps[6]).toBe(`type_text ${JSON.stringify(value)}`)
    },
  )

  it('bounds formatted metadata to 80 characters', () => {
    expect(describeComputerArgs(tool, { extra: { text: 'a'.repeat(100) } })?.meta)
      .toEqual(['extra: ' + JSON.stringify({ text: 'a'.repeat(100) }).slice(0, 79) + '…'])
  })
})

describe('computer output fixes', () => {
  it('ignores an invalid escape in a recovered halt reason', () => {
    const output = '{"executed":9,"total":40,"halt_reason":"bad\\q","steps":['
    expect(() => describeComputerOutput(output)).not.toThrow()
    expect(describeComputerOutput(output)).toMatchObject({ executed: 9, total: 40, reason: null })
  })

  it.each(['4', '4 ', '4e', '4.', '4x'])(
    'does not accept an incomplete or undelimited total: %s', total => {
      const output = '{"executed":9,"total":' + total
      expect(() => describeComputerOutput(output)).not.toThrow()
      expect(describeComputerOutput(output)).toBeNull()
    },
  )

  it('accepts numeric recovery only before a comma or closing brace', () => {
    expect(describeComputerOutput('{"executed":9 ,"total":40 ,"steps":[')?.total).toBe(40)
    expect(describeComputerOutput('{"executed":9,"total":40 } trailing')?.total).toBe(40)
    expect(describeComputerOutput('{"executed":9,"total":40,"failed_step":4')?.failedStep).toBeNull()
  })

  it.each(['halted', 'disabled', 'unverified'])(
    'rejects nested %s markers in parsed and truncated output', reason => {
      const output = JSON.stringify({ nested: { stop_reason: reason }, executed: 0, total: 40 })
      expect(userStopped(tool, output)).toBe(false)
      expect(userStopped(tool, output.slice(0, -1))).toBe(false)
    },
  )

  it.each(['disabled', 'unverified'])(
    'keeps top-level %s as a failure', reason => {
      expect(userStopped(tool, JSON.stringify({ halted: true, stop_reason: reason }))).toBe(false)
    },
  )

  it('recognizes the server prefix in full JSON and after a 3000-character cut', () => {
    const long = stopped.replace('[]', JSON.stringify([{ message: 'a'.repeat(4000) }]))
    for (const output of [stopped, long.slice(0, 3000)]) {
      expect(userStopped(tool, output)).toBe(true)
      expect(userStopped('Bash', output)).toBe(false)
    }
  })
})

describe('computer view fixes', () => {
  it('shows wrapped run_steps with its count and readable steps instead of raw output in the label', () => {
    aktifDilAyarla('tr')
    const { container } = render(<ToolBlock tool={tool} args={wrapped} summary='{"executed":40}' output={stopped} />)
    expect(container.querySelector('.tool-target')?.textContent).toBe('run_steps · 40 adım')
    fireEvent.click(screen.getByRole('button'))
    expect(container.querySelector('.tool-cu-meta')?.textContent).toBe('pid: 28425 · window_id: 122 · delay_ms: 1000')
    expect(container.querySelectorAll('.tool-cu-steps li')).toHaveLength(40)
  })

  it('translates the step count to English', () => {
    aktifDilAyarla('en')
    const { container } = render(<ToolBlock tool={tool} args={wrapped} />)
    expect(container.querySelector('.tool-target')?.textContent).toBe('run_steps · 40 steps')
  })

  it('keeps plain-text click output visible outside raw data and strips bidi from every raw block', () => {
    aktifDilAyarla('tr')
    const output = 'işlem\u202e reddedildi.'
    const { container } = render(<ToolBlock tool='computer_click' args={{ name: 'Sa\u202eve' }} output={output} summary={output} />)
    expect(container.querySelector('.tool-target')?.textContent).toBe('click')
    fireEvent.click(screen.getByRole('button'))
    const visibleOutput = container.querySelector('.tool-out > div > pre')!
    expect(visibleOutput.textContent).toBe('işlem reddedildi.')
    expect(visibleOutput.closest('details')).toBeNull()
    expect(visibleOutput.previousElementSibling?.textContent).toBe('ÇIKTI:')
    const raw = screen.getByText('Ham veri').closest('details')!
    expect(raw.open).toBe(false)
    expect(raw.querySelectorAll('pre')).toHaveLength(2)
    for (const pre of raw.querySelectorAll('pre')) expect(pre.textContent).not.toContain('\u202e')
    expect(raw.querySelector('pre')?.textContent).toContain('Save')
  })

  it('counts a stopped failure separately and marks the group only when a real failure exists', () => {
    const tools = [{ tool, success: false, output: stopped }]
    const { container, rerender } = render(<ToolGroup tools={tools} />)
    expect(container.querySelector('.tool')?.hasAttribute('data-failed')).toBe(false)
    expect(summarizeTools(tools)).toEqual([
      { key: 'tool.sum.computer', values: { sayi: 1 } },
      { key: 'tool.sum.stopped', values: { sayi: 1 } },
    ])
    rerender(<ToolGroup tools={[...tools, { tool, success: false, output: '{"stop_reason":"disabled"}' }]} />)
    expect(container.querySelector('.tool')?.getAttribute('data-failed')).toBe('true')
  })
})
