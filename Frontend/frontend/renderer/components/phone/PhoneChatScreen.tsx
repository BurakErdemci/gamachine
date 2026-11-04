import React, { useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { useVisualViewportInset, type PhoneChatTab } from '../../lib/mobile';
import { stripBidi } from '../../lib/modelText';
import type { ConvStatus } from '../../hooks/home/useChat';
import type { ContextUsage } from '../home/types';
import { ChatStatusLamp } from './ChatRow';
import { PhoneMenuSheet, type PhoneMenuSheetProps } from './PhoneMenuSheet';
import { PhoneScreenTab } from './PhoneScreenTab';

export interface PhoneChatScreenProps {
  /** The connection line, shown here only while the PC is not reachable. */
  connection: React.ReactNode;
  connected: boolean;
  title: string;
  status: ConvStatus | undefined;
  convId: number | null;
  tab: PhoneChatTab;
  onTab: (tab: PhoneChatTab) => void;
  onBack: () => void;
  /** The desktop's own thread (ChatPanel, or the empty new chat) and the chat-less request tray. */
  thread: React.ReactNode;
  tray?: React.ReactNode;
  /** The desktop's composer (AnimatedChatInput). */
  composer: React.ReactNode;
  contextUsage?: ContextUsage | null;
  /** A full-screen page (settings, profile, guide) is drawn over the shell: the Ekran tab is not watched then. */
  covered?: boolean;
  /** The Sohbet tab is shown again after the Ekran tab (the hidden thread may have grown meanwhile). */
  onThreadShown?: () => void;
  /** The chat screen was just opened: its thread is a new scroll container (a waiting card sits at the end). */
  onThreadOpened?: () => void;
  menu: Omit<PhoneMenuSheetProps, 'open' | 'onClose' | 'convId'>;
}

/** One chat on the phone: top bar, the Sohbet / Ekran tabs, and the composer fixed at the bottom. */
export function PhoneChatScreen(props: PhoneChatScreenProps) {
  const { tab, onTab, convId, contextUsage } = props;
  const { t } = useLang();
  const [menuOpen, setMenuOpen] = useState(false);
  // The on-screen keyboard covers the layout viewport's bottom: the composer pads itself above it.
  const keyboard = useVisualViewportInset(tab === 'chat');
  const memory = contextUsage
    ? `${t('strip.memory')} ${t('strip.pct', { yuzde: contextUsage.percent })}`
    : `${t('strip.memory')}: ${t('usage.noData')}`;
  const prevTab = useRef(tab);
  useEffect(() => { props.onThreadOpened?.(); }, [convId]);
  useEffect(() => {
    if (tab === 'chat' && prevTab.current !== 'chat') props.onThreadShown?.();
    prevTab.current = tab;
  }, [tab]);
  const tabBtn = (id: PhoneChatTab, label: string) => (
    <button type="button" role="tab" id={`phone-tab-${id}`} aria-selected={tab === id} aria-controls={`phone-panel-${id}`}
      className="phone-tab" onClick={() => onTab(id)}>{label}</button>
  );
  return (
    <section className="phone-chat stage paper" data-tab={tab} aria-label={props.title}>
      <header className="phone-chat-bar shell">
        <button type="button" className="phone-icon-btn phone-back" onClick={props.onBack} aria-label={t('phone.back')}>‹</button>
        <span className="phone-chat-title">{stripBidi(props.title)}</span>
        {convId != null && <ChatStatusLamp convId={convId} status={props.status} />}
        <button type="button" className="phone-icon-btn phone-more" data-testid="phone-menu" aria-haspopup="dialog"
          aria-expanded={menuOpen} onClick={() => setMenuOpen(true)} aria-label={t('phone.menu')}>⋯</button>
      </header>
      {!props.connected && props.connection}
      <div className="phone-tabs shell" role="tablist">
        {tabBtn('chat', t('phone.tabChat'))}
        {tabBtn('screen', t('phone.tabScreen'))}
      </div>
      {/* The chat stays mounted under the Ekran tab, only hidden: the draft, the scroll position and the
          thread are where the user left them. */}
      <div className="phone-thread" id="phone-panel-chat" role="tabpanel" aria-labelledby="phone-tab-chat" hidden={tab !== 'chat'}>
        {props.tray}
        {props.thread}
      </div>
      <div className="phone-composer composer-wrap" data-testid="phone-composer" hidden={tab !== 'chat'}
        style={{ '--phone-kb': `${keyboard}px` } as React.CSSProperties}>
        {props.composer}
        <p className="phone-memory" data-testid="phone-memory">{memory}</p>
      </div>
      {tab === 'screen' && (
        <div className="phone-thread" id="phone-panel-screen" role="tabpanel" aria-labelledby="phone-tab-screen">
          <PhoneScreenTab visible={!props.covered} connected={props.connected} />
        </div>
      )}
      <PhoneMenuSheet {...props.menu} convId={convId} open={menuOpen} onClose={() => setMenuOpen(false)} />
    </section>
  );
}
