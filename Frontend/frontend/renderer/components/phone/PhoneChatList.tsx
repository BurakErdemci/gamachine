import React from 'react';
import { useLang } from '../../lib/i18n';
import type { Conversation } from '../home/types';
import { type ConvStatus, useBgTasks } from '../../hooks/home/useChat';
import { STATUS_DOT, familyRootId, familyStatusOf, rootsOf } from '../../lib/convFamily';
import { shortDate } from '../home/ThreadHeader';
import { AwaitingChip, ChatStatusLamp } from './ChatRow';

export interface PhoneChatListProps {
  /** The connection line (PhoneShell's), drawn at the top. */
  connection: React.ReactNode;
  conversations: Conversation[];
  activeConvId: number | null;
  convStatus: Record<number, ConvStatus>;
  /** Requests that belong to no chat (MCP gates of another client); they count in the bar too. */
  unknownGates: number;
  projectName: string | null;
  onSwitchProject: () => void;
  onOpenSettings: () => void;
  onOpenChat: (conv: Conversation) => void;
  /** Opens the chat screen as it is, for requests that belong to no chat when there is no chat to open. */
  onOpenRequests: () => void;
  onNewChat: () => void;
  /** A new chat is being created: "+ Yeni" waits instead of showing another chat. */
  creating?: boolean;
}

const today = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** The row's time: HH:MM for today, the title block's dd.mm.yy otherwise; nothing when it does not parse. */
export const rowTime = (iso?: string | null): string => {
  if (!iso) return '';
  // The backend stores naive local "YYYY-MM-DD HH:MM:SS" (ThreadHeader.shortDate): read it as written.
  const naive = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(iso);
  if (naive && naive[1] === today()) return naive[2];
  return shortDate(iso);
};

function Row({ conv, status, active, onOpen }: { conv: Conversation; status: ConvStatus | undefined; active: boolean; onOpen: () => void }) {
  const { t } = useLang();
  const bg = useBgTasks(conv.id);
  const time = rowTime(conv.updated_at);
  return (
    <li>
      <button type="button" className="phone-row" data-testid={`phone-row-${conv.id}`} data-active={active || undefined}
        aria-current={active ? 'page' : undefined} onClick={onOpen}>
        <ChatStatusLamp convId={conv.id} status={status} />
        <span className="phone-row-main">
          <span className="phone-row-title">{conv.title}</span>
          {(status || bg.length > 0) && (
            <span className="phone-row-meta">
              {status && <span>{t(STATUS_DOT[status].label)}</span>}
              {bg.length > 0 && <span data-testid={`phone-row-bg-${conv.id}`}>{t('phone.bgTasks', { n: bg.length })}</span>}
            </span>
          )}
        </span>
        {status === 'awaiting' && <AwaitingChip />}
        {time && <time className="phone-row-time num">{time}</time>}
      </button>
    </li>
  );
}

/** The phone's start screen: connection, project, waiting approvals, the chats and a new-chat button. */
export function PhoneChatList(props: PhoneChatListProps) {
  const { conversations, activeConvId, convStatus, unknownGates, projectName, onOpenChat } = props;
  const { t } = useLang();
  // Branches live inside their chat (as on the desktop): a row stands for its whole family.
  const roots = rootsOf(conversations);
  const statusOf = (id: number) => familyStatusOf(conversations, convStatus, id);
  const activeRootId = familyRootId(conversations, activeConvId);
  const awaitingRoots = roots.filter(c => statusOf(c.id) === 'awaiting');
  // The phone has no branch tabs: a family with a waiting branch opens that branch, where its card is.
  const openTarget = (root: Conversation) =>
    conversations.find(c => convStatus[c.id] === 'awaiting' && (c.id === root.id || c.parent_id === root.id)) ?? root;
  const awaiting = awaitingRoots.length + unknownGates;
  // The bar opens the first waiting chat; requests of no chat (McpUnknownTray) show above whichever chat is
  // open, so with no chat at all it opens the empty one.
  const openAwaiting = () => {
    const first = (awaitingRoots[0] && openTarget(awaitingRoots[0])) ?? conversations.find(c => c.id === activeConvId) ?? roots[0];
    if (first) onOpenChat(first); else props.onOpenRequests();
  };
  return (
    <section className="phone-list" aria-label={t('sidebar.chats')}>
      <header className="phone-list-head shell">
        {props.connection}
        <div className="phone-list-tools">
          <button type="button" className="phone-project" onClick={props.onSwitchProject} title={t('sidebar.switchProject')}>
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5.5h4.5l1.5 1.8h8v8.2H3z" /></svg>
            <span className="phone-project-name">{projectName || 'Workspace'}</span>
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
          </button>
          <button type="button" className="phone-icon-btn" onClick={props.onOpenSettings} aria-label={t('sidebar.settings')} title={t('sidebar.settings')}>
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="10" cy="10" r="2.6" />
              <path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6" />
            </svg>
          </button>
        </div>
      </header>
      {awaiting > 0 && (
        <button type="button" className="phone-awaiting" data-testid="phone-awaiting" onClick={openAwaiting}>
          {t('phone.awaiting', { n: awaiting })}
        </button>
      )}
      <div className="phone-list-scroll paper">
        {roots.length === 0
          ? <p className="phone-empty">{t('phone.noChats')}</p>
          : (
            <ul className="phone-rows">
              {roots.map(c => (
                <Row key={c.id} conv={c} status={statusOf(c.id)} active={c.id === activeRootId} onOpen={() => onOpenChat(openTarget(c))} />
              ))}
            </ul>
          )}
      </div>
      <button type="button" className="phone-fab" data-testid="phone-new-chat" onClick={props.onNewChat} aria-label={t('phone.newChatAria')}
        disabled={props.creating} aria-busy={props.creating || undefined}>
        {props.creating ? t('phone.creating') : t('phone.newChat')}
      </button>
    </section>
  );
}
