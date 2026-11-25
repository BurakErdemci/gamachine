import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import type { QueuedView } from '../../hooks/home/useTurnQueue';

/** "1st", "2nd", "3rd", "11th"; Turkish writes "1.". */
export const ordinal = (n: number, lang: string) => {
  if (lang === 'tr') return `${n}.`;
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${suffix}`;
};

const ClockIc = () => <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 6v4.3l2.8 1.8" /></svg>;

/**
 * Messages waiting for the running turn, drawn where the user wrote them: their own bubble,
 * dimmed, with its place in line and one action, "Remove from queue" (INFO_DESIGN.md C1).
 * No edit and no "send now": to change one, take it back and write it again. When its turn
 * starts it leaves this list and comes back as an ordinary message; that change is the notice.
 */
export const QueuedMessages = ({ items, onRemove, paused, onResume }: {
  items: QueuedView[];
  onRemove: (item: QueuedView) => void;
  /** The queue stopped after a failed turn: nothing starts by itself until "Send next". */
  paused?: boolean;
  onResume?: () => void;
}) => {
  const { t, lang } = useLang();
  if (items.length === 0) return null;
  const waiting = items.filter(i => !i.sent).length;
  const firstLocal = items.find(i => !i.sent && i.source === 'local');
  return (
    <div className="queued-flow" data-testid="queued-messages">
      {items.map(item => (
        <React.Fragment key={item.key}>
          <article className="msg msg-user is-queued" data-role="user" data-testid="queued-message" data-source={item.source}>
            <div className="msg-who">{t('msg.you')}</div>
            <div className="msg-body">
              {item.images && item.images.length > 0 && (
                <div className="msg-images">{item.images.map((img, i) => <img key={i} src={img} alt="" />)}</div>
              )}
              <div className="msg-user-text"><p>{stripBidi(item.text) || t('queue.attachmentsOnly')}</p></div>
            </div>
          </article>
          <div className="queued-tag" data-testid="queued-tag">
            <ClockIc />
            {item.sent
              ? <span>{t('queue.sent')}</span>
              : <span>
                  <b>{waiting > 1 ? t('queue.queuedN', { n: ordinal(item.place ?? 1, lang) }) : t('queue.title')}</b>
                  {' — '}{paused && item.source === 'local' ? t('queue.paused') : t('queue.startsAfter')}
                </span>}
            {item.note && <span className="queued-note" role="status">{t(item.note === 'started' ? 'queue.alreadyStarted' : 'queue.removeFailed')}</span>}
            {paused && onResume && item === firstLocal && (
              <button type="button" className="queued-x" data-testid="queued-resume" onClick={onResume}>{t('queue.sendNext')}</button>
            )}
            {item.note !== 'started' && (!item.sent || item.turnEnded) && (
                <button type="button" className="queued-x" data-testid="queued-remove" onClick={() => onRemove(item)}>
                  {t('queue.delete')}
                </button>
              )}
          </div>
        </React.Fragment>
      ))}
    </div>
  );
};
