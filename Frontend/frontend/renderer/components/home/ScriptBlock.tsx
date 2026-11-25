import React, { useEffect, useMemo, useState } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import {
  countBy, fmtSeconds, groupCalls, parseScriptReply,
  type CallRow, type CallStatus, type ScriptCall, type ScriptReply,
} from '../../lib/scriptReply';

type T = (key: any, values?: Record<string, string | number>) => string;

export interface ScriptBlockProps {
  /** The run_script arguments: `code` is the program the agent wrote. */
  args?: any;
  output?: string;
  success?: boolean;
  /** The turn is still streaming and this call has no result yet. */
  live?: boolean;
  onOpenFile?: (path: string) => void;
}

const ScriptIcon = ({ className = 'ic ic-script' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 4.5C5.8 4.5 5.8 6 5.8 7.4S5.3 10 3.8 10c1.5 0 2 1.2 2 2.6s0 2.9 1.7 2.9M12.5 4.5c1.7 0 1.7 1.5 1.7 2.9s.5 2.6 2 2.6c-1.5 0-2 1.2-2 2.6s0 2.9-1.7 2.9" /></svg>
);
const Chev = () => <svg className="ic ic-sm tool-chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>;
const GrpChev = () => <svg className="ic grp-chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 6l4 4-4 4" /></svg>;
const WarnIcon = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>;
const InfoIcon = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 9v4.5M10 6.6v.1" /></svg>;

/** The verb column of a call row: which side the tool talks to, in the step list's own words. */
const verbOf = (tool: string, t: T) => /^blender/i.test(tool) ? 'Blender' : t('tool.verb.unity');

const STATUS_KEY: Record<CallStatus, string> = {
  ok: 'script.st.ok', error: 'script.st.error', refused: 'script.st.refused', timeout: 'script.st.timeout',
  unfinished: 'script.st.unfinished', notrun: 'script.st.notrun', notawaited: 'script.st.notawaited',
};
const COUNT_KEY: [CallStatus, string, 'bad' | 'warn' | null][] = [
  ['error', 'script.nFailed', 'bad'], ['refused', 'script.nRefused', 'warn'], ['timeout', 'script.nTimeout', 'warn'],
  ['unfinished', 'script.nUnfinished', 'warn'], ['notawaited', 'script.nNotAwaited', null], ['notrun', 'script.nNotRun', null],
];

type Part = { text: string; tone?: 'bad' | 'warn' | 'head' };

/** The folded line: what it did, how many calls, how it ended, how long it took. */
export function scriptSummary(reply: ScriptReply, t: T): Part[] {
  const counts = countBy(reply.calls);
  const secs = reply.seconds != null ? fmtSeconds(reply.seconds) : null;
  const parts: Part[] = [];
  const firstBad = reply.calls.find(c => c.status !== 'ok' && c.status !== 'notawaited' && c.status !== 'notrun');
  if (reply.finished) {
    parts.push({ text: t('script.ran'), tone: 'head' });
    parts.push({ text: t('script.calls', { n: reply.total }) });
    const clean = COUNT_KEY.every(([s]) => counts[s] === 0);
    if (clean) parts.push({ text: t('script.allOk') });
    else parts.push({ text: t('script.nOk', { n: counts.ok }) });
  } else if (reply.stopKind === 'timeout') {
    parts.push({ text: t('script.timedOut', { s: secs ?? '' }), tone: 'head' });
    parts.push({ text: t('script.nOk', { n: counts.ok }) });
  } else {
    parts.push({ text: firstBad ? t('script.stoppedAt', { n: firstBad.index }) : t('script.stopped'), tone: 'head' });
    if (reply.calls.length) parts.push({ text: t('script.nOk', { n: counts.ok }) });
  }
  for (const [status, key, tone] of COUNT_KEY) {
    if (counts[status]) parts.push({ text: t(key, { n: counts[status] }), ...(tone ? { tone } : {}) });
  }
  if (secs && reply.stopKind !== 'timeout') parts.push({ text: secs });
  return parts;
}

const keepSentence = (ok: number, t: T) =>
  ok === 0 ? t('script.keepNone') : ok === 1 ? t('script.keepOne') : t('script.keep', { n: ok });

/**
 * The one sentence a script that did not end clean carries: what happened, then what stays.
 * The user's first question after a stopped run is "were the earlier changes undone?"; the
 * answer is here, readable without opening the block. Null when the run was clean.
 */
export function scriptVerdict(reply: ScriptReply, t: T): { tone: 'stop' | 'soft'; head: string; tail?: string } | null {
  const okBefore = (call: ScriptCall) => reply.calls.filter(c => c.index < call.index && c.status === 'ok').length;
  const okAll = reply.calls.filter(c => c.status === 'ok').length;
  if (reply.finished) {
    const refused = reply.calls.find(c => c.status === 'refused');
    if (refused) return { tone: 'soft', head: t('script.verdict.refused', { n: refused.index, tool: refused.tool }), tail: t('script.wentOn') };
    const failed = reply.calls.find(c => c.status === 'error' || c.status === 'timeout');
    if (failed) return {
      tone: 'soft',
      head: t('script.verdict.caught', { n: failed.index, tool: failed.tool, why: failed.reason || t('script.st.error') }),
      tail: t('script.wentOn'),
    };
    return null;
  }
  if (reply.stopKind === 'timeout') {
    const during = reply.calls.find(c => c.status === 'unfinished' || c.status === 'timeout');
    const head = during
      ? t('script.verdict.timeout', { n: during.index, tool: during.tool })
      : t('script.verdict.timeoutNoCall', { s: reply.seconds != null ? fmtSeconds(reply.seconds) : '' });
    const unknown: string[] = reply.calls.filter(c => c.status === 'unfinished' || c.status === 'timeout').map(c => `#${c.index}`);
    const notAwaited = reply.calls.filter(c => c.status === 'notawaited').length;
    if (notAwaited) unknown.push(t('script.unknownNotAwaited', { n: notAwaited }));
    const keep = keepSentence(during ? okBefore(during) : okAll, t);
    const tail = unknown.length
      ? `${keep.replace(/\.$/, ';')} ${t('script.keepUnknown', { list: unknown.join(` ${t('script.and')} `) })}`
      : keep;
    return { tone: 'stop', head, tail };
  }
  const bad = reply.calls.find(c => c.status === 'refused' || c.status === 'error' || c.status === 'timeout');
  if (bad && bad.status === 'refused') {
    return { tone: 'stop', head: t('script.verdict.refused', { n: bad.index, tool: bad.tool }), tail: keepSentence(okBefore(bad), t) };
  }
  if (bad) {
    const why = stripBidi(bad.reason || reply.error || reply.kindText).replace(/\.$/, '');
    return { tone: 'stop', head: `${t('script.verdict.failed', { n: bad.index, why })}.`, tail: keepSentence(okBefore(bad), t) };
  }
  if (reply.calls.length === 0) {
    const why = stripBidi(reply.error || reply.kindText).replace(/\.$/, '');
    return { tone: 'stop', head: t('script.verdict.noCall', { why }) };
  }
  const why = stripBidi(reply.error || reply.kindText).replace(/\.$/, '');
  return { tone: 'stop', head: `${why.charAt(0).toUpperCase()}${why.slice(1)}.`, tail: keepSentence(okAll, t) };
}

const Verdict = ({ v }: { v: NonNullable<ReturnType<typeof scriptVerdict>> }) => (
  <p className="script-verdict" data-tone={v.tone === 'soft' ? 'soft' : undefined} data-testid="script-verdict">
    {v.tone === 'soft' ? <InfoIcon /> : <WarnIcon />}
    <span>{v.head}{v.tail && <> <em>{v.tail}</em></>}</span>
  </p>
);

const statusText = (row: CallRow, t: T) =>
  row.count > 1 ? t('script.nOk', { n: row.count }) : t(STATUS_KEY[row.status]);

const CallLine = ({ call, t }: { call: ScriptCall; t: T }) => (
  <li className="tool-step" data-s={call.status}>
    <div className="tool-step-btn">
      <span className="call-n">#{call.index}</span>
      <span className="tool-verb">{verbOf(call.tool, t)}</span>
      <code className="tool-target">{stripBidi(call.tool)}</code>
      <span className="call-s" data-s={call.status}
        title={call.status === 'notawaited' ? t('script.notawaitedTip') : undefined}>{t(STATUS_KEY[call.status])}</span>
      <span />
    </div>
    {call.reason && call.status !== 'ok' && <p className="call-why">{stripBidi(call.reason)}</p>}
  </li>
);

/** An opened run shows its first calls; the rest of a long same-result run waits behind one line. */
const GROUP_HEAD = 3;

const GroupLine = ({ row, t }: { row: CallRow; t: T }) => {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const shown = all || row.calls.length <= GROUP_HEAD + 1 ? row.calls : row.calls.slice(0, GROUP_HEAD);
  return (
    <li className="tool-step call-group" data-s={row.status} data-open={open || undefined}>
      <button type="button" className="tool-step-btn" aria-expanded={open} onClick={() => setOpen(v => !v)}>
        <span className="call-n">#{row.from}–{row.to}</span>
        <span className="tool-verb">{verbOf(row.tools[0], t)}</span>
        <code className="tool-target">{stripBidi(row.tools.join(', '))}</code>
        <span className="call-s" data-s={row.status}>{statusText(row, t)}</span>
        <GrpChev />
      </button>
      {open && (
        <ol className="script-calls script-calls-in">
          {shown.map(c => <CallLine key={c.index} call={c} t={t} />)}
          {shown.length < row.calls.length && (
            <li className="call-more">
              <button type="button" className="ev-link" onClick={() => setAll(true)}>
                {t('script.moreSame', { n: row.calls.length - shown.length })}
              </button>
            </li>
          )}
        </ol>
      )}
    </li>
  );
};

const Program = ({ code, t }: { code: string; t: T }) => {
  const [open, setOpen] = useState(false);
  const lines = code.replace(/\n+$/, '').split('\n').length;
  return (
    <details className="script-code" data-testid="script-program" open={open}>
      <summary onClick={e => { e.preventDefault(); setOpen(v => !v); }}><GrpChev />{t('script.program', { n: lines })}</summary>
      {open && (
        <SyntaxHighlighter
          language="javascript"
          useInlineStyles={false}
          codeTagProps={{ className: 'language-javascript' }}
          PreTag="pre"
          className="code-body custom-scrollbar"
        >
          {stripBidi(code.replace(/\n+$/, ''))}
        </SyntaxHighlighter>
      )}
    </details>
  );
};

const RESULT_CAP = 600;

/**
 * A run_script call as a kind of tool row (INFO_DESIGN.md A): folded, one sentence that says
 * what the script did; opened, the result, the calls in order (ok runs folded into one line,
 * anything else on its own line with its reason) and, folded once more, the program.
 * A reply the parser does not recognise is shown as raw output rather than guessed at.
 */
export const ScriptBlock = ({ args, output, success, live, onOpenFile }: ScriptBlockProps) => {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const running = !!live && success === undefined && !(output && output.trim());
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);

  const reply = useMemo(() => (running ? null : parseScriptReply(output)), [running, output]);
  const code = args && typeof args === 'object' && typeof args.code === 'string' ? args.code : '';
  const verdict = reply ? scriptVerdict(reply, t) : null;
  const failed = reply ? !reply.finished : success === false;

  let parts: Part[];
  if (running) parts = [{ text: t('script.running'), tone: 'head' }, { text: fmtSeconds(Math.max(0, (now - startedAt) / 1000)) }];
  else if (reply) parts = scriptSummary(reply, t);
  else parts = [{ text: t('script.ran'), tone: 'head' }, ...(success === false ? [{ text: t('script.st.error'), tone: 'bad' as const }] : [])];

  const state = running ? 'running' : failed ? 'error' : 'done';
  const rows = reply ? groupCalls(reply.calls) : [];
  const result = reply?.result ? stripBidi(reply.result) : '';
  const fullOutput = reply?.fullOutputPath && onOpenFile ? reply.fullOutputPath : null;

  return (
    <div className="script-wrap" data-testid="script-block" data-state={state}>
      <div className="tool-row">
        {/* Open state is React's: the body is built only when opened (a 200-call list costs
            nothing folded) and the verdict line moves into it, never shown twice. */}
        <details className="tool fold-host is-script" data-state={state} data-failed={failed || undefined} open={open}>
          <summary onClick={e => { e.preventDefault(); setOpen(v => !v); }}>
            {running ? <span className="run-dot" aria-hidden="true" /> : <ScriptIcon />}
            <span className="tool-text num" data-testid="script-summary">
              {parts.map((p, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <span className="tool-sep"> · </span>}
                  {p.tone === 'head' ? <b>{p.text}</b>
                    : p.tone === 'bad' ? <span className="t-bad">{p.text}</span>
                    : p.tone === 'warn' ? <span className="t-warn">{p.text}</span>
                    : p.text}
                </React.Fragment>
              ))}
            </span>
            <Chev />
          </summary>
          <div className="fold"><div className="fold-in"><div className="script-body">
            {open && <>
              {verdict && <Verdict v={verdict} />}
              {result && (
                <p className="script-result">
                  {result.length > RESULT_CAP ? `${result.slice(0, RESULT_CAP)}…` : result}
                </p>
              )}
              {reply && reply.calls.length > 0 && (
                <div>
                  <p className="script-k">
                    {t('script.callsInOrder')}<span className="sep">·</span><span className="num">{reply.total}</span>
                    {reply.shortened && <><span className="sep">·</span>{t('script.callsShortened')}</>}
                  </p>
                  <ol className="script-calls">
                    {rows.map(row => row.count > 1
                      ? <GroupLine key={row.from} row={row} t={t} />
                      : <CallLine key={row.from} call={row.calls[0]} t={t} />)}
                  </ol>
                </div>
              )}
              {!reply && !running && (
                output && output.trim()
                  ? <div className="tool-out custom-scrollbar" data-testid="script-raw"><p className="tool-out-k">{t('tool.output')}</p><pre>{stripBidi(output)}</pre></div>
                  : <p className="script-k">{t('script.noOutput')}</p>
              )}
              {code && <Program code={code} t={t} />}
            </>}
          </div></div></div>
        </details>
        {fullOutput && (
          <button type="button" className="tool-open" onClick={() => onOpenFile!(fullOutput)}>
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="1.2" /><path d="M12.5 4v12" /></svg>
            {t('script.fullOutput')}
          </button>
        )}
      </div>
      {/* Folded, the sentence sits under the row; opened, it is the body's first line (never twice). */}
      {verdict && !open && <Verdict v={verdict} />}
    </div>
  );
};
