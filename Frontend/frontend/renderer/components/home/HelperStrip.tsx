import React, { useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { modelFamily } from '../../lib/modelFamily';
import { ModelLogo } from '../ui/ModelLogos';
import { isScriptTool } from '../../lib/scriptReply';
import type { FuseState, Helper } from '../../hooks/home/useHelpers';

type T = (key: any, values?: Record<string, string | number>) => string;

/** A helper's state as the strip shows it: the backend's status, or "asks" while a card waits. */
export type HelperShown = Helper['status'] | 'asks';

/** "no activity" starts here (INFO_DESIGN.md D6); the backend's progress record uses the same. */
export const IDLE_WARN_S = 90;
/** The chips give way to one summary sentence from this many helpers on. */
export const STRIP_CHIP_MAX = 4;

const STATUS_WORD: Record<HelperShown, string> = {
  running: 'helper.st.running', idle: 'helper.st.idle', done: 'helper.st.done', stopped: 'helper.st.stopped',
  interrupted: 'helper.st.interrupted', error: 'helper.st.error', asks: 'helper.st.asks',
};

export const shownStatus = (h: Helper, asking?: ReadonlySet<number>): HelperShown =>
  (h.status === 'running' || h.status === 'idle') && (asking?.has(h.chat_id) || h.progress?.phase === 'waiting_approval')
    ? 'asks' : h.status;

/** m:ss, the timer format the app's working line uses. */
export const clock = (s: number) => {
  const v = Math.max(0, Math.floor(s));
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`;
};
/** "40 s" under a minute, then m:ss. */
const secs = (s: number) => (s < 10 ? `${Math.max(0, s).toFixed(1)} s` : s < 60 ? `${Math.floor(s)} s` : clock(s));

export const familyOf = (h: Pick<Helper, 'model_name' | 'provider_type'>) => modelFamily(h.model_name, h.provider_type);
const LOGO_OF = { claude: 'claude', codex: 'openai', gemini: 'gemini' } as const;

/** The model's mark in the model's colour (the author line's logo system; no new colour). */
export const HelperLogo = ({ helper, size = 16 }: { helper: Pick<Helper, 'model_name' | 'provider_type'>; size?: number }) => {
  const fam = familyOf(helper);
  return (
    <span className="mlogo" data-m={fam} aria-hidden="true">
      <ModelLogo provider={fam === 'other' ? (helper.provider_type || '') : LOGO_OF[fam]} size={size} />
    </span>
  );
};

export const StatusLamp = ({ status, t }: { status: HelperShown; t: T }) => (
  <span className="st" data-s={status} data-testid="helper-status"><span className="st-l" aria-hidden="true" />{t(STATUS_WORD[status])}</span>
);

export interface DoingLine { text: string; tone?: 'asks' | 'stale' | 'bad'; stale?: boolean }

/**
 * The "what is it doing" line (INFO_DESIGN.md D6). `drift` is the seconds since the record was
 * read: running counters keep ticking between polls and are corrected when the next one lands.
 * `finished` adds the closing sentence a done/failed/stopped helper gets in the list; chips leave
 * it out (a finished helper's chip is quiet).
 */
export function doingLine(h: Helper, t: T, drift = 0, asking?: ReadonlySet<number>, finished = false, ignoreIdle = false): DoingLine | null {
  const p = h.progress || null;
  const shown = shownStatus(h, asking);
  const steps = p?.steps_done ?? 0;
  if (shown === 'asks') {
    const waited = (p?.card?.waiting_s ?? 0) + drift;
    return { text: `${t('helper.doing.asks')} · ${clock(waited)}`, tone: 'asks' };
  }
  if (h.status === 'running' || (h.status === 'idle' && p?.phase === 'queued')) {
    if (p?.phase === 'queued') return { text: t('helper.doing.queued') };
    if (!ignoreIdle && p?.idle_s != null && p.idle_s + drift >= IDLE_WARN_S) {
      return { text: t('helper.doing.idle', { n: Math.max(1, Math.round((p.idle_s + drift) / 60)) }), tone: 'stale', stale: true };
    }
    if (!p) return null;
    const parts: string[] = [];
    if (p.phase === 'running_tool' && p.step) {
      parts.push(isScriptTool(p.step.tool) ? t('helper.doing.script')
        : stripBidi([p.step.tool, p.step.target].filter(Boolean).join(' ')));
      if (p.step.seconds != null) parts.push(secs(p.step.seconds + drift));
    } else if (p.phase === 'thinking' || p.phase === 'writing') {
      parts.push(t(p.phase === 'thinking' ? 'helper.doing.thinking' : 'helper.doing.writing'));
      if (p.step?.seconds != null) parts.push(secs(p.step.seconds + drift));
    } else if (p.phase === 'running_tool') {
      parts.push(t('helper.doing.tool'));
    }
    if (steps) parts.push(t('helper.doing.steps', { n: steps }));
    return parts.length ? { text: parts.join(' · ') } : null;
  }
  if (!finished) return null;
  // The backend keeps a progress record only while a helper runs: a finished one has no step
  // count to tell, and "0 steps" would be a wrong number, not a missing one.
  const counted = !!p && steps > 0;
  if (h.status === 'done') return { text: counted ? t('helper.doing.done', { n: steps }) : t('helper.doing.doneBare') };
  if (h.status === 'error') {
    const failed = [...(p?.last_steps ?? [])].reverse().find(s => s.ok === false);
    const why = stripBidi((h.last_result || '').split('\n')[0]).slice(0, 120);
    if (failed) return { text: t('helper.doing.failedAt', { what: stripBidi([failed.tool, failed.target].filter(Boolean).join(' ')) }) + (why ? `: ${why}` : ''), tone: 'bad' };
    return { text: why ? t('helper.doing.failed', { why }) : t('helper.st.error'), tone: 'bad' };
  }
  if (h.status === 'interrupted') return { text: counted ? t('helper.doing.interrupted', { n: steps + 1 }) : t('helper.doing.interruptedBare') };
  if (h.status === 'stopped') return { text: counted ? t('helper.doing.stopped', { n: steps }) : t('helper.doing.stoppedBare') };
  return null;
}

const ClockIc = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 6v4.3l2.8 1.8" /></svg>;
const CrewIc = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="7" cy="7.5" r="2.4" /><circle cx="13.5" cy="7.5" r="2" /><path d="M2.8 15.5c.6-2.6 2.2-4 4.2-4s3.6 1.4 4.2 4M12 11.6c2.2-.3 4 1 4.6 3.9" /></svg>;
const StopIc = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="1.5" /></svg>;
const ChevIc = ({ up }: { up?: boolean }) => <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d={up ? 'M6 12l4-4 4 4' : 'M6 8l4 4 4-4'} /></svg>;

export const Doing = ({ line }: { line: DoingLine }) => (
  <span className="crew-doing" data-tone={line.tone} data-testid="helper-doing">
    {line.stale && <ClockIc />}{line.text}
  </span>
);

interface StripProps {
  helpers: Helper[];
  fetchedAt: number;
  fuse?: FuseState | null;
  /** Chat ids of helpers whose approval card is on the parent's screen right now. */
  asking?: ReadonlySet<number>;
  openAgentId?: string | null;
  onOpen: (h: Helper) => void;
  onStop: (agentId: string) => void;
  onStopAll: () => void;
}

/** The hover card: the whole progress record, the last thing the helper said included. */
const HelperPop = ({ h, t, drift, asking, onOpen, onStop }: {
  h: Helper; t: T; drift: number; asking?: ReadonlySet<number>; onOpen: () => void; onStop: () => void;
}) => {
  const p = h.progress || null;
  // "Now" is what it is doing; how long it has been quiet has its own row (Last activity).
  const now = doingLine(h, t, drift, asking, true, true);
  const idle = p?.idle_s != null ? p.idle_s + (h.status === 'running' ? drift : 0) : null;
  const meta = [h.model_name, h.effort].filter(Boolean).join(' · ');
  return (
    <div className="crew-pop" role="dialog" aria-label={h.name} data-testid="helper-pop" onClick={e => e.stopPropagation()}>
      <div className="crew-pop-h"><HelperLogo helper={h} size={14} /><b>{stripBidi(h.name)}</b>{meta && <span>{stripBidi(meta)}</span>}</div>
      {now && <><span className="crew-pop-k">{t('helper.pop.now')}</span><span className="crew-pop-v">{now.text}</span></>}
      <span className="crew-pop-k">{t('helper.pop.steps')}</span>
      <span className="crew-pop-v num">{t('helper.pop.stepsVal', { d: p?.steps_done ?? 0, f: p?.steps_failed ?? 0 })}</span>
      {p?.elapsed_s != null && <>
        <span className="crew-pop-k">{t('helper.pop.elapsed')}</span>
        <span className="crew-pop-v num">{clock(p.elapsed_s + (h.status === 'running' ? drift : 0))}</span>
      </>}
      {p?.last_said && <><span className="crew-pop-k">{t('helper.pop.said')}</span><span className="crew-pop-v">“{stripBidi(p.last_said)}”</span></>}
      {idle != null && <>
        <span className="crew-pop-k">{t('helper.pop.activity')}</span>
        <span className="crew-pop-v num" data-tone={idle >= IDLE_WARN_S ? 'stale' : undefined}>
          {idle < 5 ? t('helper.pop.justNow') : t('helper.pop.ago', { t: idle < 60 ? `${Math.floor(idle)} s` : `${Math.round(idle / 60)} min` })}
        </span>
      </>}
      <div className="crew-pop-acts">
        <button type="button" className="ev-link" onClick={onOpen}>{t('helper.openConv')}</button>
        {h.status === 'running' && <button type="button" className="ev-link" onClick={onStop}>{t('helper.stop')}</button>}
      </div>
    </div>
  );
};

const Chip = ({ h, t, drift, asking, open, onOpen, onStop }: {
  h: Helper; t: T; drift: number; asking?: ReadonlySet<number>; open: boolean; onOpen: () => void; onStop: () => void;
}) => {
  const [pop, setPop] = useState(false);
  const touch = useRef(false);
  const shown = shownStatus(h, asking);
  const line = doingLine(h, t, drift, asking);
  return (
    <span className={`crew-chip${open ? ' is-open' : ''}`} data-s={shown} data-m={familyOf(h)} data-testid={`helper-chip-${h.agent_id}`}
      onMouseEnter={() => { if (!touch.current) setPop(true); }}
      onMouseLeave={() => setPop(false)}
      onPointerDown={e => { touch.current = e.pointerType === 'touch'; }}>
      {/* A tap opens the card first on touch screens (no hover there); a click opens the conversation. */}
      <button type="button" className="crew-chip-top" aria-pressed={open}
        onClick={() => { if (touch.current) setPop(v => !v); else onOpen(); }}>
        <HelperLogo helper={h} />
        <span className="nm">{stripBidi(h.name)}</span>
        <StatusLamp status={shown} t={t} />
      </button>
      {h.status === 'running' && (
        <button type="button" className="crew-x" title={t('helper.stopOne', { name: h.name })} aria-label={t('helper.stopOne', { name: h.name })}
          onClick={onStop}><StopIc /></button>
      )}
      {line && <Doing line={line} />}
      {pop && <HelperPop h={h} t={t} drift={drift} asking={asking} onOpen={onOpen} onStop={onStop} />}
    </span>
  );
};

/**
 * The helpers strip under the chat header (INFO_DESIGN.md D1, D6): one chip per helper up to
 * four, then one summary sentence that keeps only the helpers needing a look as chips, and a
 * full list on demand. Nothing at all for a chat without helpers.
 */
export const HelperStrip = ({ helpers, fetchedAt, fuse, asking, openAgentId, onOpen, onStop, onStopAll }: StripProps) => {
  const { t } = useLang();
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const ticking = helpers.some(h => h.status === 'running');
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [ticking]);
  if (helpers.length === 0) return null;
  const drift = Math.max(0, (now - fetchedAt) / 1000);
  const many = helpers.length > STRIP_CHIP_MAX;
  const shown = helpers.map(h => shownStatus(h, asking));
  const problem = helpers.filter((_, i) => shown[i] === 'asks' || shown[i] === 'error');
  const chips = many ? problem : helpers;
  const count = (s: HelperShown) => shown.filter(v => v === s).length;
  const rest = count('idle') + count('stopped') + count('interrupted');

  return (
    <div className={`crew${expanded ? ' is-expanded' : ''}${many ? ' is-many' : ''}`} data-testid="helper-strip">
      <span className="crew-k" title={t('helper.strip')}><CrewIc /><span className="crew-k-t">{t('helper.strip')}</span></span>
      {chips.map(h => (
        <Chip key={h.agent_id} h={h} t={t} drift={drift} asking={asking} open={openAgentId === h.agent_id}
          onOpen={() => onOpen(h)} onStop={() => onStop(h.agent_id)} />
      ))}
      {!many && fuse?.tripped && <span className="st" data-s="asks">{t('helper.sum.paused')}</span>}
      {!many && (
        <button type="button" className="crew-more" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}>
          {expanded ? t('helper.hide') : t('helper.all')}<ChevIc up={expanded} />
        </button>
      )}
      {many && (
        <div className="crew-sum-row">
          <span className="crew-sum num" data-testid="helper-summary">
            <span>{t('helper.count', { n: helpers.length })}</span>
            {count('running') > 0 && <><span className="sep">·</span><span className="st" data-s="running"><span className="st-l" />{t('helper.sum.working', { n: count('running') })}</span></>}
            {count('done') > 0 && <><span className="sep">·</span><span className="st" data-s="done"><span className="st-l" />{t('helper.sum.done', { n: count('done') })}</span></>}
            {count('error') > 0 && <><span className="sep">·</span><span className="st" data-s="error"><span className="st-l" />{t('helper.sum.error', { n: count('error') })}</span></>}
            {count('asks') > 0 && <><span className="sep">·</span><span className="st" data-s="asks"><span className="st-l" />{t('helper.sum.asks', { n: count('asks') })}</span></>}
            {rest > 0 && <><span className="sep">·</span><span>{t('helper.sum.rest', { n: rest })}</span></>}
            {fuse?.tripped && <><span className="sep">·</span><span className="st" data-s="asks"><span className="st-l" />{t('helper.sum.paused')}</span></>}
          </span>
          <button type="button" className="crew-more" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}>
            {expanded ? t('helper.hide') : t('helper.showAll')}<ChevIc up={expanded} />
          </button>
        </div>
      )}
      {expanded && (
        <div className="crew-list" role="table" data-testid="helper-list">
          <div className="h" role="row">
            <span>{t('helper.col.helper')}</span><span>{t('helper.col.model')}</span><span>{t('helper.col.effort')}</span>
            <span>{t('helper.col.status')}</span><span>{t('helper.col.time')}</span><span />
          </div>
          {helpers.map((h, i) => {
            const line = doingLine(h, t, drift, asking, true);
            const said = h.status === 'running' ? h.progress?.last_said : null;
            const elapsed = h.progress?.elapsed_s;
            return (
              <div key={h.agent_id} role="row" data-testid={`helper-row-${h.agent_id}`}>
                <span className="nm-cell"><HelperLogo helper={h} /><span className="nm">{stripBidi(h.name)}</span></span>
                <span className="md">{stripBidi(h.model_name || '')}</span>
                <span className="md">{stripBidi(h.effort || '')}</span>
                <span><StatusLamp status={shown[i]} t={t} /></span>
                <span className="md num">{elapsed != null ? clock(elapsed + (h.status === 'running' ? drift : 0)) : ''}</span>
                <span className="acts">
                  <button type="button" className="crew-act" onClick={() => onOpen(h)}>{t('helper.open')}</button>
                  {h.status === 'running' && (
                    <button type="button" className="crew-x" title={t('helper.stopOne', { name: h.name })} aria-label={t('helper.stopOne', { name: h.name })}
                      onClick={() => onStop(h.agent_id)}><StopIc /></button>
                  )}
                </span>
                {(line || said) && (
                  <span className="doing-row">
                    {line && <Doing line={line} />}
                    {said && <span className="crew-said">“{stripBidi(said)}”</span>}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {expanded && helpers.some(h => h.status === 'running') && (
        <div className="crew-foot">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onStopAll}><StopIc />{t('helper.stopAll')}</button>
        </div>
      )}
    </div>
  );
};
