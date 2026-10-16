import { afterEach, describe, expect, it } from 'vitest'
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { describeComputerArgs, describeComputerOutput } from '../renderer/lib/computerStep'
import { ToolBlock } from '../renderer/components/home/ToolBlock'
import { aktifDilAyarla } from '../renderer/lib/i18n'
import { stripBidi } from '../renderer/lib/modelText'

afterEach(() => { cleanup(); aktifDilAyarla(null) })

describe('describeComputerArgs', () => {
  it('describes explicit, shorthand and check steps in order', () => {
    expect(describeComputerArgs('computer_run_steps', {
      pid: 42, window_id: 7, delay_ms: 10, observe: {},
      steps: [
        { tool: 'click', args: { role: 'button', name: 'Save', nth: 0 } },
        { type_text: { text: 'hello', role: 'textbox' } },
        { set_value: { value: 'world', role: 'textbox', name: 'Title', nth: 2 } },
        { wait_for: { role: 'dialog' }, expect: { name: 'Done' }, timeout_ms: 100 },
      ],
    })).toEqual({
      meta: ['pid: 42', 'window_id: 7', 'delay_ms: 10'],
      steps: [
        'click → button "Save" #0',
        'type_text "hello" → textbox',
        'set_value "world" → textbox "Title" #2',
        'wait_for {"role":"dialog"} expect {"name":"Done"}',
      ],
    })
  })

  it('describes keyboard actions and token and coordinate targets', () => {
    expect(describeComputerArgs('mcp__gamachineComputer__computer_run_steps', { steps: [
      { hotkey: { keys: ['CTRL', 'S'] } },
      { tool: 'press_key', args: { key: 'A', modifiers: ['CTRL', 'SHIFT'] } },
      { press_key: { key: 'ENTER' } },
      { click: { element_token: 'abcdefghijk' } },
      { drag: { x: 0, y: 25 } },
    ] })?.steps).toEqual([
      'hotkey CTRL+S', 'press_key CTRL+SHIFT+A', 'press_key ENTER',
      'click → token abcdefgh', 'drag → (0, 25)',
    ])
  })

  it('bounds serialized text and compact check values with an ellipsis', () => {
    const result = describeComputerArgs('computer_run_steps', { steps: [
      { type_text: { text: 'a'.repeat(100) } },
      { expect: { name: 'b'.repeat(100) } },
    ] })!
    expect(result.steps[0]).toBe('type_text ' + ('"' + 'a'.repeat(78) + '…'))
    expect(result.steps[1]).toBe('expect ' + JSON.stringify({ name: 'b'.repeat(100) }).slice(0, 79) + '…')
  })

  it('keeps non-batch computer arguments as meta, quoting only strings', () => {
    expect(describeComputerArgs('computer_click', {
      pid: 42, name: 'Save "as"', enabled: false, extra: null, observe: {}, steps: [],
    })).toEqual({
      meta: ['pid: 42', 'name: "Save \\"as\\""', 'enabled: false', 'extra: null'], steps: [],
    })
  })

  it.each(['Bash', 'mcp__unityMCP__run_steps', 'other_computer_run_steps'])(
    'rejects non-computer tools (%s)', tool => {
      expect(describeComputerArgs(tool, { pid: 42 })).toBeNull()
    },
  )

  it.each([null, undefined, [], 'text', 42, new Date(0)].map(args => [args]))('rejects non-plain arguments (%s)', args => {
    expect(describeComputerArgs('computer_run_steps', args)).toBeNull()
  })
})

describe('describeComputerOutput', () => {
  it('reads a full report and limits result messages', () => {
    expect(describeComputerOutput(JSON.stringify({
      executed: 2, total: 2, failed_step: null, ok: true,
      steps: [{ index: 0, ok: true, message: 'clicked' },
        { index: 1, ok: true, message: 'a'.repeat(150) }],
    }))).toEqual({
      executed: 2, total: 2, stopped: false, reason: null, failedStep: null,
      steps: [{ index: 0, ok: true, message: 'clicked' },
        { index: 1, ok: true, message: 'a'.repeat(119) + '…' }],
    })
  })

  it('reads a stopped report', () => {
    expect(describeComputerOutput(JSON.stringify({
      executed: 9, total: 40, stop_reason: 'halted', halt_reason: 'Stopped by user', steps: [],
    }))).toEqual({
      executed: 9, total: 40, stopped: true, reason: 'Stopped by user', failedStep: null, steps: [],
    })
  })

  it('keeps the zero-based failed step and does not treat disabled as user stopped', () => {
    expect(describeComputerOutput('{"executed":1,"total":4,"failed_step":0,"stop_reason":"disabled"}'))
      .toEqual({ executed: 1, total: 4, stopped: false, reason: null, failedStep: 0, steps: [] })
  })

  it('recovers header fields from truncated JSON without guessing partial steps', () => {
    expect(describeComputerOutput(
      '{"halted":true,"stop_reason":"halted","halt_reason":"User said \\"stop\\"","executed":9,"total":40,"failed_step":8,"steps":[{"index":0',
    )).toEqual({
      executed: 9, total: 40, stopped: true, reason: 'User said "stop"', failedStep: 8, steps: [],
    })
    expect(describeComputerOutput('{"executed":0,"total":4,"failed_step":null,"steps":[')?.failedStep).toBeNull()
  })

  it.each([
    undefined, '', '{"executed":9,"steps":[', '{"total":40,"steps":[',
    '{"executed":"9","total":40}', '{"executed":9}', 'null', '[]',
  ])('requires numeric executed and total (%s)', output => {
    expect(describeComputerOutput(output)).toBeNull()
  })
})

describe('ToolBlock readable computer steps', () => {
  it('opens readable steps and stopped results with the raw data initially collapsed', () => {
    aktifDilAyarla('tr')
    const args = { pid: 42, steps: [{ click: { role: 'button', name: 'Sa\u202eve' } }] }
    const output = JSON.stringify({
      executed: 9, total: 40, stop_reason: 'halted',
      steps: [{ index: 0, ok: true, message: 'cli\u202ecked' },
        { index: 1, ok: false, message: 'missed' }],
    })
    const { container } = render(<ul><ToolBlock tool="computer_run_steps" args={args} output={output} success={false} /></ul>)
    expect(container.querySelector('.tool-out')).toBeNull()
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('click → button "Save"')).toBeTruthy()
    expect(screen.getByText('pid: 42')).toBeTruthy()
    expect(screen.getByText('9/40 adım çalıştı · kullanıcı durdurdu')).toBeTruthy()
    expect(screen.getByText('✓ clicked').getAttribute('data-ok')).toBe('true')
    expect(screen.getByText('✗ missed').getAttribute('data-ok')).toBe('false')
    const raw = screen.getByText('Ham veri').closest('details')!
    expect(raw.open).toBe(false)
    expect(raw.querySelectorAll('pre')).toHaveLength(2)
    expect(raw.querySelectorAll('pre')[0].textContent).toBe(stripBidi(JSON.stringify(args, null, 2)))
    expect(raw.querySelectorAll('pre')[1].textContent).toBe(stripBidi(output))
    fireEvent.click(screen.getByText('Ham veri'))
    expect(raw.open).toBe(true)
  })

  it('uses the English translations and converts failed step zero to step one', () => {
    aktifDilAyarla('en')
    render(<ToolBlock tool="computer_run_steps" output='{"executed":1,"total":4,"failed_step":0}' />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('1/4 steps ran · failed at step 1')).toBeTruthy()
    expect(screen.getByText('Raw data')).toBeTruthy()
  })

  it('strips bidi controls from meta and shows unparseable output above raw data', () => {
    aktifDilAyarla('tr')
    const { container } = render(<ToolBlock tool="computer_click" args={{ name: 'Sa\u202eve' }} output="unreadable" />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('name: "Save"')).toBeTruthy()
    expect(container.querySelector('.tool-cu-result')).toBeNull()
    const outputs = screen.getAllByText('unreadable')
    expect(outputs).toHaveLength(2)
    expect(outputs[0].closest('details')).toBeNull()
    expect(outputs[1].closest('details')).toBeTruthy()
  })

  it('leaves non-computer raw parameter and output blocks unchanged', () => {
    const args = { command: 'echo hello' }
    const { container } = render(<ToolBlock tool="Bash" args={args} output="hello" />)
    fireEvent.click(screen.getByRole('button'))
    expect(container.querySelector('details')).toBeNull()
    const blocks = container.querySelectorAll('pre')
    expect(blocks).toHaveLength(2)
    expect(blocks[0].textContent).toBe(JSON.stringify(args, null, 2))
    expect(blocks[1].textContent).toBe('hello')
  })
})
