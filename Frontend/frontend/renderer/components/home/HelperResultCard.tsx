import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import type { Message } from './types';
import type { Helper } from '../../hooks/home/useHelpers';
import { HelperLogo, StatusLamp, clock, familyOf, type HelperShown } from './HelperStrip';

/** Backend `subagents.RESULT_MARKER` / `FUSE_MARKER`: how a stored system message says what it is. */
export const HELPER_RESULT_MARKER = '[SUB-AGENT RESULT]';
export const HELPER_FUSE_MARKER = '[SUB-AGENT FUSE]';

export const isHelperResult = (msg: Pick<Message, 'role' | 'content'>) =>
  msg.role === 'system' && typeof msg.content === 'string' && msg.content.startsWith(HELPER_RESULT_MARKER);
export const isHelperFuse = (msg: Pick<Message, 'role' | 'content'>) =>
  msg.role === 'system' && typeof msg.content === 'string' && msg.content.startsWith(HELPER_FUSE_MARKER);

export interface HelperResult { agentId: string; name: string; model: string; status: HelperShown; body: string }

const RESULT_HEAD = /^\[SUB-AGENT RESULT\] #(\S+) "(.*)" \((.*)\): (\w+)[^\n]*\n?/;
const STATUSES = ['running', 'idle', 'done', 'error', 'stopped', 'interrupted'];

/**
 * Backend `subagents.format_result`:
 *   [SUB-AGENT RESULT] #id "name" (model): status
 *   <one line telling the model this is not the user>
 *
 *   <body>
 * The second line is for the model only and never shown.
 */
export function parseHelperResult(content: string): HelperResult | null {
  const m = RESULT_HEAD.exec(content);
  if (!m) return null;
  const rest = content.slice(m[0].length);
  const gap = rest.indexOf('\n\n');
  const body = (gap >= 0 ? rest.slice(gap + 2) : rest).trim();
  return {
    agentId: m[1],
    name: m[2],
    model: m[3],
    status: (STATUSES.includes(m[4]) ? m[4] : 'done') as HelperShown,
    body,
  };
}

const PREVIEW_LINES = 4;
const PREVIEW_CHARS = 420;

/**
 * A helper's result as it lands in the parent chat (INFO_DESIGN.md D4): a sibling of the mail
 * note, edged in the helper's model colour rather than amber, its first lines with "Show more",
 * and the "Started on its own" line under it when the parent went on because of it.
 */
export const HelperResultCard = ({ content, helper, startedTurn, onOpen }: {
  content: string;
  /** The live row from /subagents/list, when the strip has it: provider and time come from there. */
  helper?: Helper | null;
  /** The parent's next turn began because of this result. */
  startedTurn?: boolean;
  onOpen?: (agentId: string) => void;
}) => {
  const { t } = useLang();
  const [more, setMore] = useState(false);
  const parsed = parseHelperResult(content);
  if (!parsed) return null;
  const lines = parsed.body.split('\n');
  const long = lines.length > PREVIEW_LINES || parsed.body.length > PREVIEW_CHARS;
  const preview = long && !more
    ? lines.slice(0, PREVIEW_LINES).join('\n').slice(0, PREVIEW_CHARS).trimEnd() + '…'
    : parsed.body;
  const ident = { model_name: helper?.model_name ?? parsed.model, provider_type: helper?.provider_type ?? null };
  const elapsed = helper?.progress?.elapsed_s;
  return <>
    <div className="msg-note is-agent" data-m={familyOf(ident)} data-testid="helper-result">
      <div className="msg-note-head">
        <HelperLogo helper={ident} size={14} />
        <span className="nm">{stripBidi(parsed.name)}</span>
        <span className="md">{stripBidi(helper?.model_name || parsed.model)}</span>
        <StatusLamp status={parsed.status} t={t} />
        {elapsed != null && <span className="md num">{clock(elapsed)}</span>}
      </div>
      <p className="msg-note-body">{stripBidi(preview)}</p>
      {(long || onOpen) && (
        <div className="agent-acts">
          {long && <button type="button" className="ev-link" onClick={() => setMore(v => !v)}>{more ? t('helper.showLess') : t('helper.showMore')}</button>}
          {onOpen && <button type="button" className="ev-link" onClick={() => onOpen(parsed.agentId)}>{t('helper.openConv')}</button>}
        </div>
      )}
    </div>
    {startedTurn && (
      <div className="msg-event is-wake" data-role="system" data-testid="helper-wake">
        <RefreshCw size={13} className="shrink-0" aria-hidden="true" />
        <span className="msg-event-k">{t('helper.wakeK')}</span>
        <span className="msg-event-v">· {t('helper.wakeResult', { name: stripBidi(parsed.name) })}</span>
      </div>
    )}
  </>;
};
