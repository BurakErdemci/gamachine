/**
 * The run_script tool row (4.1.0 design A): the reply text from Backend/app/script_tool.py
 * (format_reply / _summary / calls_line) read back into a verdict, a call list and a program,
 * with a raw fallback when the text is not recognised.
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { parseScriptReply, parseCallsLine, groupCalls, isScriptTool } from '../renderer/lib/scriptReply'
import { ScriptBlock } from '../renderer/components/home/ScriptBlock'
import { ToolGroup } from '../renderer/components/home/ToolGroup'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const OK = [
  'run_script finished in 4.2 s · 32 tool call(s) (32 ok)',
  'Tool calls in order:',
  '1 find_gameobjects ok; 2-31 add_component ok (30); 32 save_scene ok',
  'Result:',
  'Added Rigidbody to 30 enemies in Level_02 and saved the scene.',
].join('\n')

const STOPPED = [
  'run_script stopped: the script threw an error after 2.0 s. 13 of 14 tool call(s) completed before it stopped; their effects stay (13 ok, 1 error).',
  'Error:',
  'Error: Enemy_13 already has a Rigidbody',
  'Tool calls in order:',
  '1 find_gameobjects ok; 2-13 add_component ok (12); 14 add_component error (Enemy_13 already has a Rigidbody from its prefab)',
].join('\n')

const CAUGHT = [
  'run_script finished in 4.6 s · 33 tool call(s) (32 ok, 1 error)',
  'Tool calls in order:',
  '1-8 add_component ok (8); 9 delete_gameobject error (not found); 10-33 add_component ok (24)',
  'Result:',
  '(the script returned nothing)',
].join('\n')

const REFUSED = [
  'run_script stopped: a tool call was refused after 4.0 s. 2 of 3 tool call(s) completed before it stopped; their effects stay (2 ok, 1 refused).',
  'Error:',
  'delete_gameobject was refused, so the script was ended there: no',
  'Tool calls in order:',
  '1-2 find_gameobjects ok (2); 3 delete_gameobject refused (no)',
].join('\n')

const TIMEOUT = [
  'run_script stopped: the script hit its time limit after 60.0 s. 18 of 24 tool call(s) completed before it stopped; their effects stay (18 ok, 1 unfinished, 2 not run, 3 not run).',
  'Error:',
  'script ran past its 60 s limit',
  'Tool calls in order:',
  '1-18 add_component ok (18); 19 bake_navmesh unfinished (still running when the run ended; it may still take effect); 20 add_component unfinished (still running when the run ended; it may still take effect) (not awaited); 21 add_component not run (the script returned without awaiting it); 22 add_component not run (the script ended before it started)',
  'Note:',
  '1 tool call(s) were not awaited by the script, so they were not run; await them (or use Promise.all) to run them',
].join('\n')

const CODE = 'const e = await tools.find_gameobjects({ tag: "Enemy" });\nfor (const x of e) {\n  await tools.add_component({ target: x });\n}\nreturn "done";'

beforeEach(() => aktifDilAyarla('en'))
afterEach(() => { cleanup(); aktifDilAyarla('tr') })

describe('parseScriptReply', () => {
  it('reads a clean run: time, total, every call, the result', () => {
    const r = parseScriptReply(OK)!
    expect(r.finished).toBe(true)
    expect(r.seconds).toBe(4.2)
    expect(r.total).toBe(32)
    expect(r.calls).toHaveLength(32)
    expect(r.calls.every(c => c.status === 'ok')).toBe(true)
    expect(r.calls[1]).toEqual({ index: 2, tool: 'add_component', status: 'ok' })
    expect(r.result).toBe('Added Rigidbody to 30 enemies in Level_02 and saved the scene.')
    expect(r.fullOutputPath).toBeNull()
  })

  it('reads a stop: the kind, the error, the failed call with its reason', () => {
    const r = parseScriptReply(STOPPED)!
    expect(r.finished).toBe(false)
    expect(r.stopKind).toBe('error')
    expect(r.kindText).toBe('the script threw an error')
    expect(r.error).toBe('Error: Enemy_13 already has a Rigidbody')
    expect(r.calls.at(-1)).toEqual({ index: 14, tool: 'add_component', status: 'error', reason: 'Enemy_13 already has a Rigidbody from its prefab' })
  })

  it('tells refused and timed-out runs apart, and the unknown calls of a timeout', () => {
    expect(parseScriptReply(REFUSED)!.stopKind).toBe('refused')
    const r = parseScriptReply(TIMEOUT)!
    expect(r.stopKind).toBe('timeout')
    expect(r.calls.map(c => c.status).slice(18)).toEqual(['unfinished', 'notawaited', 'notrun', 'notrun'])
    expect(r.notes).toHaveLength(1)
  })

  it('keeps the saved file and a "returned nothing" result apart from real text', () => {
    const r = parseScriptReply(CAUGHT + '\nFull output (9000 bytes) saved to: C:/p/.gamachine/script-runs/run-1.txt')!
    expect(r.fullOutputPath).toBe('C:/p/.gamachine/script-runs/run-1.txt')
    expect(r.result).toBeNull()
  })

  it('reads a shortened call list without inventing the missing middle', () => {
    const { calls, shortened } = parseCallsLine('1-3 a ok (3); 4 b error (x … (list shortened; full list in the file below) … ror (y); 90 c ok')
    expect(shortened).toBe(true)
    expect(calls.map(c => c.index)).toEqual([1, 2, 3, 90])
  })

  it('returns null for text it does not recognise', () => {
    expect(parseScriptReply('Error: sandbox exploded')).toBeNull()
    expect(parseScriptReply('')).toBeNull()
    expect(parseScriptReply(undefined)).toBeNull()
    expect(parseScriptReply('run_script finished in 1.0 s · 2 tool call(s) (2 ok)\nTool calls in order:\nnonsense')).toBeNull()
  })

  it('names the tool by the Claude server and the unityai server', () => {
    expect(isScriptTool('mcp__gamachineScript__run_script')).toBe(true)
    expect(isScriptTool('run_script')).toBe(true)
    expect(isScriptTool('mcp__unityai__run_script')).toBe(true)
    expect(isScriptTool('mcp__unityMCP__manage_scene')).toBe(false)
  })
})

describe('groupCalls', () => {
  it('folds ok runs of at most two tools; anything not ok stands alone', () => {
    const rows = groupCalls(parseScriptReply(STOPPED)!.calls)
    expect(rows.map(r => [r.from, r.to, r.status])).toEqual([[1, 13, 'ok'], [14, 14, 'error']])
    expect(rows[0].tools).toEqual(['find_gameobjects', 'add_component'])
    const three = groupCalls([
      { index: 1, tool: 'a', status: 'ok' }, { index: 2, tool: 'b', status: 'ok' }, { index: 3, tool: 'c', status: 'ok' },
    ])
    expect(three.map(r => r.count)).toEqual([2, 1])
  })
})

const summary = () => screen.getByTestId('script-summary').textContent

describe('ScriptBlock', () => {
  it('a clean run is one quiet line: what, how many, all ok, how long', () => {
    render(<ScriptBlock args={{ code: CODE }} output={OK} success />)
    expect(summary()).toBe('Ran a script · 32 calls · all ok · 4.2 s')
    expect(screen.queryByTestId('script-verdict')).toBeNull()
  })

  it('opened: the result, the calls folded by run, the program folded once more', () => {
    const { container } = render(<ScriptBlock args={{ code: CODE }} output={OK} success />)
    fireEvent.click(container.querySelector('summary')!)
    expect(screen.getByText('Added Rigidbody to 30 enemies in Level_02 and saved the scene.')).toBeTruthy()
    const rows = container.querySelectorAll('.script-calls > li')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('#1–31')
    expect(rows[0].textContent).toContain('31 ok')
    fireEvent.click(rows[0].querySelector('button')!)
    // A long same-result run opens on its first calls; the rest wait behind one line.
    expect(container.querySelectorAll('.script-calls-in > li.tool-step')).toHaveLength(3)
    fireEvent.click(screen.getByText('… 28 more, same result'))
    expect(container.querySelectorAll('.script-calls-in > li.tool-step')).toHaveLength(31)
    const program = screen.getByTestId('script-program')
    expect(program.querySelector('summary')!.textContent).toBe('Program · 5 lines · JavaScript')
    expect(program.querySelector('pre')).toBeNull()
    fireEvent.click(program.querySelector('summary')!)
    expect(program.querySelector('pre')?.textContent).toContain('tools.find_gameobjects')
  })

  it('a stopped run says where and what stays, under the row while folded', () => {
    const { container } = render(<ScriptBlock args={{ code: CODE }} output={STOPPED} success={false} />)
    expect(summary()).toBe('Script stopped at call 14 · 13 ok · 1 failed · 2.0 s')
    expect(container.querySelector('.t-bad')?.textContent).toBe('1 failed')
    const verdict = screen.getByTestId('script-verdict')
    // The verdict names the error the script ended with; the call's own reason stays on its line.
    expect(verdict.textContent).toBe('Stopped at #14: Enemy_13 already has a Rigidbody. The 13 changes before it stay.')
    // Opened, the sentence moves into the body: it is never shown twice.
    fireEvent.click(container.querySelector('summary')!)
    expect(screen.getAllByTestId('script-verdict')).toHaveLength(1)
    expect(container.querySelector('.script-body [data-testid="script-verdict"]')).toBeTruthy()
    expect(container.querySelector('.call-why')?.textContent).toBe('Enemy_13 already has a Rigidbody from its prefab')
  })

  it('a mixed run that finished: the failure is counted and told calmly', () => {
    render(<ScriptBlock output={CAUGHT} success />)
    expect(summary()).toBe('Ran a script · 33 calls · 32 ok · 1 failed · 4.6 s')
    const verdict = screen.getByTestId('script-verdict')
    expect(verdict.dataset.tone).toBe('soft')
    expect(verdict.textContent).toBe('#9 (delete_gameobject) failed: not found The script went on without it.')
  })

  it('a refused call and a timeout each get their own sentence', () => {
    const { unmount } = render(<ScriptBlock output={REFUSED} success={false} />)
    expect(summary()).toBe('Script stopped at call 3 · 2 ok · 1 refused by you · 4.0 s')
    expect(screen.getByTestId('script-verdict').textContent).toBe('You refused #3 (delete_gameobject). The 2 changes before it stay.')
    unmount()
    render(<ScriptBlock output={TIMEOUT} success={false} />)
    expect(summary()).toBe('Script timed out after 60 s · 18 ok · 1 unfinished · 1 not awaited · 2 not run')
    expect(screen.getByTestId('script-verdict').textContent).toBe(
      'Time ran out during #19 (bake_navmesh). The 18 changes before it stay; #19 and the 1 not-awaited calls may or may not have happened.')
  })

  it('text it cannot read is shown raw, never guessed at', () => {
    const { container } = render(<ScriptBlock args={{ code: CODE }} output="Error: sandbox exploded" success={false} />)
    expect(summary()).toBe('Ran a script · failed')
    fireEvent.click(container.querySelector('summary')!)
    expect(screen.getByTestId('script-raw').textContent).toContain('Error: sandbox exploded')
    expect(screen.getByTestId('script-program')).toBeTruthy()
  })

  it('while it runs: "Running a script" and the time, no progress bar', () => {
    const { container } = render(<ScriptBlock args={{ code: CODE }} live />)
    expect(summary()).toMatch(/^Running a script · \d+\.\d s$/)
    expect(container.querySelector('.run-dot')).toBeTruthy()
    expect(screen.getByTestId('script-block').dataset.state).toBe('running')
  })

  it('"Full output" opens the saved file in the workspace', () => {
    const opened: string[] = []
    render(<ScriptBlock output={OK + '\nFull output (9000 bytes) saved to: C:/p/run-1.txt'} success onOpenFile={p => opened.push(p)} />)
    fireEvent.click(screen.getByText('Full output'))
    expect(opened).toEqual(['C:/p/run-1.txt'])
  })
})

// Turn 2, audit F5. Both texts are Backend/app/script_tool.py format_reply output, generated with
// the blind audit's gen_verdict_cases.py against this lane's backend.
const CAUGHT_THEN_THROWN = 'run_script stopped: the script threw an error after 3.1 s. 12 of 13 tool call(s) completed before it stopped; their effects stay (12 ok, 1 error).\nError:\nTypeError: x is undefined\nTool calls in order:\n1-2 get_scene_info ok (2); 3 write_file error (disk full); 4-13 manage_gameobject ok (10)'
const UNCAUGHT = 'run_script stopped: the script threw an error after 3.1 s. 2 of 3 tool call(s) completed before it stopped; their effects stay (2 ok, 1 error).\nError:\nTypeError: x is undefined\nTool calls in order:\n1-2 get_scene_info ok (2); 3 write_file error (disk full)'

describe('a script that threw (audit F5)', () => {
  it('a failure it caught earlier is not where it stopped: the thrown error is named, every ok call stays', () => {
    const { container } = render(<ScriptBlock output={CAUGHT_THEN_THROWN} success={false} />)
    expect(summary()).toBe('Script stopped · 12 ok · 1 failed · 3.1 s')
    expect(screen.getByTestId('script-verdict').textContent).toBe('Stopped on an error: TypeError: x is undefined. The 12 changes before it stay.')
    fireEvent.click(container.querySelector('summary')!)
    const failed = container.querySelector('.script-calls > li[data-s="error"]')!
    expect(failed.textContent).toContain('#3')
    expect(failed.querySelector('.call-why')?.textContent).toBe('disk full')
  })

  it('a failed last call is where it stopped, under the error the script threw', () => {
    render(<ScriptBlock output={UNCAUGHT} success={false} />)
    expect(summary()).toBe('Script stopped at call 3 · 2 ok · 1 failed · 3.1 s')
    expect(screen.getByTestId('script-verdict').textContent).toBe('Stopped at #3: TypeError: x is undefined. The 2 changes before it stay.')
  })
})

describe('"Console:" inside the returned value (audit F5)', () => {
  const head = 'run_script finished in 0.1 s · 0 tool call(s)'
  it('one "Console:" after the result is the logs', () => {
    const r = parseScriptReply(`${head}\nResult:\nSummary\nConsole:\nActual runtime log`)!
    expect(r.result).toBe('Summary')
    expect(r.console).toBe('Actual runtime log')
  })

  it('two of them cannot be told apart: the reply is shown raw, never split wrong', () => {
    const text = `${head}\nResult:\nSummary\nConsole:\nReturned text\nConsole:\nActual runtime log`
    expect(parseScriptReply(text)).toBeNull()
    const { container } = render(<ScriptBlock output={text} success />)
    fireEvent.click(container.querySelector('summary')!)
    expect(screen.getByTestId('script-raw').textContent).toContain('Returned text')
  })
})

describe('ToolGroup with a script', () => {
  it('draws the script as its own row between the ordinary step rows, in order', () => {
    const { container } = render(<ToolGroup tools={[
      { tool: 'Read', args: { file_path: '/a/x.cs' }, success: true, output: 'x' },
      { tool: 'mcp__gamachineScript__run_script', args: { code: CODE }, success: true, output: OK },
      { tool: 'Bash', args: { command: 'ls' }, success: true, output: '' },
    ]} />)
    const kinds = Array.from(container.querySelectorAll('[data-testid="tool-row"], [data-testid="script-block"]'))
      .map(el => el.getAttribute('data-testid'))
    expect(kinds).toEqual(['tool-row', 'script-block', 'tool-row'])
  })

  it('a turn without a script keeps the one summary row', () => {
    const { container } = render(<ToolGroup tools={[{ tool: 'Read', args: { file_path: '/a/x.cs' }, success: true }]} />)
    expect(container.querySelectorAll('[data-testid="tool-row"]')).toHaveLength(1)
    expect(container.querySelector('[data-testid="script-block"]')).toBeNull()
  })
})
