import React, { useSyncExternalStore } from 'react';
import { useLang } from '../../lib/i18n';
import { remoteSession, type RemoteSession, type RemoteView } from '../../lib/remote/bootstrap';
import { useMarkPhoneShell, type usePhoneRoute } from '../../lib/mobile';
import type { Conversation } from '../home/types';
import { PhoneChatList, type PhoneChatListProps } from './PhoneChatList';
import { PhoneChatScreen, type PhoneChatScreenProps } from './PhoneChatScreen';

type Connection = Pick<RemoteView, 'connection' | 'lastSeen'>;
type SessionLike = Pick<RemoteSession, 'subscribe' | 'view'>;
/** The connection the phone shows when it has no remote session (it then talks to nothing remote). */
const READY: Connection = { connection: 'ready', lastSeen: null };
const noSubscribe = () => () => {};

/** The paired link's state (RemoteSession.view), live; `ready` without a session. */
function useConnection(session: SessionLike | null): Connection {
  return useSyncExternalStore(
    session ? (cb: () => void) => session.subscribe(cb) : noSubscribe,
    () => session?.view ?? READY,
    () => session?.view ?? READY,
  );
}

/** "● Bağlı", or offline with the last time the PC was seen; what is on screen stays, marked as possibly stale. */
function ConnectionLine({ connection, lastSeen }: Connection) {
  const { t, lang } = useLang();
  const time = lastSeen ? new Date(lastSeen).toLocaleTimeString(lang === 'tr' ? 'tr-TR' : 'en-GB', { hour: '2-digit', minute: '2-digit' }) : null;
  const text = connection === 'ready' ? t('phone.connected')
    : connection === 'offline' ? t('remoteWeb.offline') + (time ? t('phone.lastSeen', { time }) : '')
      : connection === 'rejected' ? t('remoteWeb.rejected')
        : t('phone.connecting');
  return (
    <p className="phone-conn" data-testid="phone-conn" data-state={connection} role="status">
      <span className="phone-conn-t">{text}</span>
      {connection !== 'ready' && <span className="phone-conn-stale">{t('phone.stale')}</span>}
    </p>
  );
}

export interface PhoneShellProps {
  route: ReturnType<typeof usePhoneRoute>;
  conversations: Conversation[];
  activeConvId: number | null;
  convStatus: PhoneChatListProps['convStatus'];
  unknownGates: number;
  projectName: string | null;
  onSwitchProject: () => void;
  onOpenSettings: () => void;
  selectConversation: (conv: Conversation) => unknown;
  createNewConversation: () => unknown;
  chat: Pick<PhoneChatScreenProps, 'thread' | 'tray' | 'composer' | 'contextUsage' | 'menu'>;
  /** Tests pass a fake; the page reads the running remote session. */
  session?: SessionLike | null;
}

/**
 * The web remote at phone width (home.tsx, `data-phone`): the chat list or one chat, around the
 * desktop's own pieces (chat rows' lamp, ChatPanel, the composer, the live view). The connection
 * line replaces RemoteGate's fixed banner here.
 */
export function PhoneShell(props: PhoneShellProps) {
  const { route, conversations, activeConvId, convStatus } = props;
  const { t } = useLang();
  useMarkPhoneShell();
  const conn = useConnection(props.session === undefined ? remoteSession() : props.session);
  const line = <ConnectionLine {...conn} />;
  const open = (conv: Conversation) => { void props.selectConversation(conv); route.openChat(); };
  const startNew = () => { void props.createNewConversation(); route.openChat(); };

  if (route.screen === 'chat') {
    const active = conversations.find(c => c.id === activeConvId) ?? null;
    return (
      <div className="phone-shell" data-screen="chat" data-stale={conn.connection !== 'ready' || undefined}>
        <PhoneChatScreen
          {...props.chat}
          connection={line}
          connected={conn.connection === 'ready'}
          title={active?.title || t('phone.newChatAria')}
          status={activeConvId == null ? undefined : convStatus[activeConvId]}
          convId={activeConvId}
          tab={route.chatTab}
          onTab={route.setChatTab}
          onBack={route.back}
        />
      </div>
    );
  }
  return (
    <div className="phone-shell" data-screen="list" data-stale={conn.connection !== 'ready' || undefined}>
      <PhoneChatList
        connection={line}
        conversations={conversations}
        activeConvId={activeConvId}
        convStatus={convStatus}
        unknownGates={props.unknownGates}
        projectName={props.projectName}
        onSwitchProject={props.onSwitchProject}
        onOpenSettings={props.onOpenSettings}
        onOpenChat={open}
        onNewChat={startNew}
      />
    </div>
  );
}
