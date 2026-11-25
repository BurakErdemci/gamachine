/**
 * Reads the text a run_script call returns (Backend/app/script_tool.py: format_reply, _render,
 * _summary, calls_line) back into parts the script block can draw. The text is written for the
 * model, so this parser is strict: anything it does not recognise returns null and the block
 * falls back to showing the raw output.
 *
 * Shape of the text (sections are "Title:\nbody", the first has no title):
 *   run_script finished in 2.3 s · 14 tool call(s) (14 ok)
 *   run_script stopped: the script threw an error after 3.1 s. 13 of 14 tool call(s) ...
 *   Error: / Tool calls in order: / Note: (repeatable) / Result: / Console:
 *   [Full output (N bytes) saved to: <path>]
 */

export type CallStatus = 'ok' | 'error' | 'refused' | 'timeout' | 'unfinished' | 'notrun' | 'notawaited';

export interface ScriptCall {
  index: number;
  tool: string;
  status: CallStatus;
  reason?: string;
}

export interface ScriptReply {
  /** The script returned (status ok); false when it stopped. */
  finished: boolean;
  /** The backend's stop sentence ("the script threw an error"); empty when finished. */
  kindText: string;
  /** "refused" | "timeout" | "error": which stop sentence family this is. */
  stopKind: 'refused' | 'timeout' | 'error' | null;
  seconds: number | null;
  /** Calls the summary line counts; can exceed `calls.length` when the list was shortened. */
  total: number;
  calls: ScriptCall[];
  /** The call list in the reply was cut in the middle; the full list is in the saved file. */
  shortened: boolean;
  error: string;
  notes: string[];
  result: string | null;
  console: string;
  fullOutputPath: string | null;
}

const STATUS_WORDS = ['ok', 'error', 'refused', 'timeout', 'unfinished', 'not run'] as const;
const HEADERS = ['Error', 'Tool calls in order', 'Note', 'Result', 'Console'] as const;
const FULL_RE = /^Full output \(\d+ bytes\) saved to: (.+)$/;
const FULL_FAIL_RE = /^Full output could not be saved: /;
const SHORTENED = ' … (list shortened; full list in the file below) … ';

/** The tool's names: the Claude SDK in-process server, and `run_script` on the unityai server for the CLIs. */
export const isScriptTool = (name?: string | null): boolean =>
  !!name && (name === 'run_script' || name === 'mcp__gamachineScript__run_script' || /__run_script$/.test(name));

/** One part of calls_line: "3 get_scene_info ok", "1-13 unity_find ok (13)", "14 x error (why)". */
const PART_RE = new RegExp(
  String.raw`(?:^|; )(\d+)(?:-(\d+))? (\S+) (` + STATUS_WORDS.map(s => s.replace(' ', ' ')).join('|') + String.raw`)(?=$|;| \()`,
  'g',
);

export function parseCallsLine(line: string): { calls: ScriptCall[]; shortened: boolean } {
  const shortened = line.includes(SHORTENED);
  const pieces = shortened ? line.split(SHORTENED) : [line];
  const calls: ScriptCall[] = [];
  pieces.forEach((piece, pieceIndex) => {
    // The cut lands mid-part: in the tail, skip up to the first whole "; " boundary.
    let text = piece;
    if (shortened && pieceIndex > 0) {
      const at = text.indexOf('; ');
      text = at >= 0 ? text.slice(at + 2) : '';
    }
    const matches = [...text.matchAll(PART_RE)];
    matches.forEach((m, i) => {
      const end = i + 1 < matches.length ? matches[i + 1].index! : text.length;
      let rest = text.slice(m.index! + m[0].length, end).trim();
      // The head of a shortened list may end mid-part: drop a part whose brackets do not close.
      if (shortened && pieceIndex === 0 && i === matches.length - 1 && (rest.match(/\(/g)?.length ?? 0) !== (rest.match(/\)/g)?.length ?? 0)) return;
      const from = Number(m[1]);
      const to = m[2] ? Number(m[2]) : from;
      const word = m[4];
      let unawaited = false;
      if (rest.endsWith('(not awaited)')) { unawaited = true; rest = rest.slice(0, -'(not awaited)'.length).trim(); }
      let reason = '';
      if (rest.startsWith('(') && rest.endsWith(')')) reason = rest.slice(1, -1);
      if (word === 'ok') {
        for (let n = from; n <= to; n++) calls.push({ index: n, tool: m[3], status: unawaited ? 'notawaited' : 'ok' });
        return;
      }
      let status: CallStatus = word === 'not run' ? 'notrun' : word as CallStatus;
      // Started, never awaited, and the run ended under it: its effect is unknown.
      if (unawaited && (status === 'unfinished' || status === 'timeout')) status = 'notawaited';
      calls.push({ index: from, tool: m[3], status, ...(reason ? { reason } : {}) });
    });
  });
  return { calls, shortened };
}

export function parseScriptReply(text?: string | null): ScriptReply | null {
  if (!text || typeof text !== 'string') return null;
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const first = lines[0] ?? '';
  let finished: boolean;
  let kindText = '';
  let seconds: number | null = null;
  let total = 0;
  const done = /^run_script finished in ([\d.]+) s · (\d+) tool call\(s\)/.exec(first);
  const stopped = /^run_script stopped: (.+?) after ([\d.]+) s\. (.*)$/.exec(first);
  if (done) {
    finished = true;
    seconds = Number(done[1]);
    total = Number(done[2]);
  } else if (stopped) {
    finished = false;
    kindText = stopped[1];
    seconds = Number(stopped[2]);
    const n = /(\d+) of (\d+) tool call\(s\)/.exec(stopped[3]);
    total = n ? Number(n[2]) : 0;
  } else {
    return null;
  }

  let fullOutputPath: string | null = null;
  let end = lines.length;
  while (end > 1 && lines[end - 1].trim() === '') end--;
  const last = lines[end - 1] ?? '';
  const full = FULL_RE.exec(last);
  if (full) { fullOutputPath = full[1].trim(); end--; }
  else if (FULL_FAIL_RE.test(last)) end--;

  // Sections in their fixed order; once "Result:" starts only "Console:" can follow, so a
  // returned value that itself contains "Note:" stays part of the value. A returned value can also
  // hold a line "Console:"; with two such lines after "Result:" the reply alone cannot say which
  // one starts the real logs (audit F5), so the whole reply is shown raw instead of split wrong.
  const resultAt = lines.indexOf('Result:', 1);
  if (resultAt >= 0 && resultAt < end && lines.slice(resultAt + 1, end).filter(l => l === 'Console:').length > 1) return null;
  const sections: { title: string; body: string[] }[] = [];
  let seenResult = false;
  for (let i = 1; i < end; i++) {
    const line = lines[i];
    const header = HEADERS.find(h => line === `${h}:`);
    const allowed = header && (!seenResult || header === 'Console');
    if (allowed) {
      if (header === 'Result') seenResult = true;
      sections.push({ title: header!, body: [] });
    } else if (sections.length) {
      sections[sections.length - 1].body.push(line);
    } else {
      return null;
    }
  }
  const body = (title: string) => sections.filter(s => s.title === title).map(s => s.body.join('\n'));
  const callsText = body('Tool calls in order')[0] ?? '';
  const { calls, shortened } = callsText ? parseCallsLine(callsText) : { calls: [], shortened: false };
  if (callsText && calls.length === 0) return null;
  const error = body('Error')[0] ?? '';
  const result = body('Result')[0] ?? null;
  const k = kindText.toLowerCase();
  const stopKind = finished ? null
    : /refused/.test(k) ? 'refused'
    : /time limit|did not answer in time/.test(k) ? 'timeout'
    : 'error';
  return {
    finished,
    kindText,
    stopKind,
    seconds,
    total: Math.max(total, calls.length),
    calls,
    shortened,
    error,
    notes: body('Note'),
    result: result === '(the script returned nothing)' ? null : result,
    console: body('Console')[0] ?? '',
    fullOutputPath,
  };
}

/** A row of the call list: one call, or a run of ok calls folded into one line. */
export interface CallRow {
  from: number;
  to: number;
  tools: string[];
  status: CallStatus;
  count: number;
  reason?: string;
  calls: ScriptCall[];
}

/**
 * Consecutive ok calls fold into one row. The rule the design settled on (INFO_DESIGN.md, open
 * point 2): same status, at most two different tool names in one row; anything not ok breaks the
 * run and stands on its own line, so the eye goes straight to it.
 */
export function groupCalls(calls: ScriptCall[]): CallRow[] {
  const rows: CallRow[] = [];
  for (const call of calls) {
    const prev = rows[rows.length - 1];
    const joins = prev && call.status === 'ok' && prev.status === 'ok' && prev.to + 1 === call.index
      && (prev.tools.includes(call.tool) || prev.tools.length < 2);
    if (joins) {
      prev.to = call.index;
      prev.count++;
      prev.calls.push(call);
      if (!prev.tools.includes(call.tool)) prev.tools.push(call.tool);
    } else {
      rows.push({ from: call.index, to: call.index, tools: [call.tool], status: call.status, count: 1, reason: call.reason, calls: [call] });
    }
  }
  return rows;
}

export function countBy(calls: ScriptCall[]): Record<CallStatus, number> {
  const out: Record<CallStatus, number> = { ok: 0, error: 0, refused: 0, timeout: 0, unfinished: 0, notrun: 0, notawaited: 0 };
  for (const c of calls) out[c.status]++;
  return out;
}

/** The summary's duration: "4.2 s" under ten seconds, "60 s" under two minutes, then "2:05". */
export const fmtSeconds = (s: number): string => {
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  const whole = Math.round(s);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};
