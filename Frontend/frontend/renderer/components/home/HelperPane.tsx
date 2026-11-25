import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ToolGroup } from './ToolGroup';
import { clockOf } from './ChatPanel';
import type { Message } from './types';
import type { Helper } from '../../hooks/home/useHelpers';
import { HelperLogo, StatusLamp, doingLine, familyOf, shownStatus } from './HelperStrip';

export const HELPER_PANE_POLL_MS = 2000;

type Http = { get: (...a: any[]) => Promise<any> };

const EyeIc = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z" /><circle cx="10" cy="10" r="2.2" /></svg>;

/** "{name}" drawn bold inside the sentence, in the sentence's own word order. */
const withName = (sentence: string, name: string) => {
  const [before, after = ''] = sentence.split('\u0000');
  return <>{before}<b>{name}</b>{after}</>;
};

/**
 * One helper's conversation, read only, as a workspace tab (INFO_DESIGN.md D2): the same
 * message and tool components as the chat, a line on top saying whose work it is and how to
 * reach it, its state and Stop. No composer: the helper is steered through the main agent.
 * Re-read every 2 s while the helper works, so the two conversations can be watched side by side.
 */
export const HelperPane = ({ API, helper, parentId, onStop, http = axios as unknown as Http, asking, fetchedAt }: {
  API?: string;
  helper: Helper;
  parentId: number | null;
  onStop: (agentId: string) => void;
  http?: Http;
  asking?: ReadonlySet<number>;
  fetchedAt?: number;
}) => {
  const { t } = useLang();
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [failed, setFailed] = useState(false);
  const chatRef = useRef(helper.chat_id);
  chatRef.current = helper.chat_id;
  const threadRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const id = chatRef.current;
    if (!API || id == null) return;
    try {
      const res = await http.get(`${API}/conversations/${id}/messages`);
      if (chatRef.current !== id) return;
      setMessages(Array.isArray(res?.data) ? res.data : []);
      setFailed(false);
    } catch {
      if (chatRef.current === id) setFailed(true);
    }
  }, [API, http]);

  useEffect(() => { setMessages(null); setFailed(false); void load(); }, [helper.chat_id, load]);
  const running = helper.status === 'running';
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => { void load(); }, HELPER_PANE_POLL_MS);
    return () => clearInterval(id);
  }, [running, load]);
  // One more read when it stops, so the final answer shows.
  useEffect(() => { if (!running) void load(); }, [running, load]);
  const count = messages?.length ?? 0;
  // Follow the newest message (the pane's own scroller; the chat's scroll hook is the chat's).
  useEffect(() => { const el = threadRef.current; if (el) el.scrollTop = el.scrollHeight; }, [count]);

  const status = shownStatus(helper, asking);
  const line = running ? doingLine(helper, t, fetchedAt ? Math.max(0, (Date.now() - fetchedAt) / 1000) : 0, asking) : null;
  const fam = familyOf(helper);
  const who = [helper.name, helper.model_name].filter(Boolean).join(' · ');

  return (
    <div className="helper-pane" data-testid="helper-pane" data-agent={helper.agent_id}>
      <div className="ro-bar">
        <EyeIc />
        <span className="grow">
          {withName(t('helper.readOnly', { name: '\u0000', n: parentId ?? '' }), stripBidi(helper.name))}
        </span>
        <StatusLamp status={status} t={t} />
        {running && (
          <button type="button" className="ro-stop" data-testid="helper-pane-stop" onClick={() => onStop(helper.agent_id)}>
            {t('helper.stop')}
          </button>
        )}
      </div>
      <div className="helper-thread custom-scrollbar" ref={threadRef}>
        <div className="thread-col">
          {messages === null && !failed && <p className="helper-note" role="status">{t('helper.loading')}</p>}
          {failed && messages === null && <p className="helper-note">{t('helper.loadError')}</p>}
          {messages && messages.length === 0 && <p className="helper-note">{t('helper.empty')}</p>}
          {messages?.map((msg, i) => msg.role === 'user' ? (
            <article key={msg.id ?? i} className="msg msg-user" data-role="user">
              <div className="msg-who">{t('helper.mainAgent')}{clockOf(msg.timestamp) && <time>{clockOf(msg.timestamp)}</time>}</div>
              <div className="msg-body"><div className="msg-user-text"><MarkdownRenderer content={msg.content} /></div></div>
            </article>
          ) : msg.role === 'assistant' ? (
            <article key={msg.id ?? i} className="msg msg-ai" data-role="assistant" data-m={fam}>
              <div className="msg-who">
                <span className="model-dot" aria-hidden="true" />
                <span className="who-name">{stripBidi(who)}</span>
                {clockOf(msg.timestamp) && <time>{clockOf(msg.timestamp)}</time>}
              </div>
              <div className="msg-stack">
                <ToolGroup tools={msg.tool_calls} live={running && i === messages.length - 1} />
                <ToolGroup tools={msg.tools} live={running && i === messages.length - 1} />
                {msg.content && <div className="msg-body"><MarkdownRenderer content={msg.content} /></div>}
              </div>
            </article>
          ) : null)}
          {line && (
            <div className="working is-on" aria-live="polite">
              <span className="dots" aria-hidden="true"><i /><i /><i /></span>
              <span className="step"><span className="step-text">{line.text}</span></span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

/** The tab's label: the helper's mark and name (mockup: "✦ LEVEL-DESIGNER"). */
export const HelperTabLabel = ({ helper }: { helper: Helper }) => (
  <span className="ws-tab-helper"><HelperLogo helper={helper} size={12} />{stripBidi(helper.name)}</span>
);
