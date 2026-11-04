import React from 'react';
import { useLang } from '../../lib/i18n';
import type { ConvStatus } from '../../hooks/home/useChat';
import { STATUS_DOT } from '../../lib/convFamily';

// Lamp per chat status: the mockup's `.status-*` classes, coloured by tokens.
const LAMP: Record<ConvStatus, string> = {
  running: 'status-running',
  awaiting: 'status-pending',
  unread: 'status-ok',
};

/** A chat row's status lamp, shared by the desktop sidebar and the phone's chat list. */
export function ChatStatusLamp({ convId, status }: { convId: number; status: ConvStatus | undefined }) {
  const { t } = useLang();
  if (!status) return <span className="status status-idle" aria-hidden="true" />;
  return (
    <span
      data-testid={`conv-status-${convId}`}
      role="img"
      title={t(STATUS_DOT[status].label)}
      aria-label={t(STATUS_DOT[status].label)}
      className={`status ${LAMP[status]}`}
    />
  );
}

/** The waiting chip: one signal per state, the lamp steps aside for it (CSS m5). */
export function AwaitingChip() {
  return (
    <span className="badge" aria-hidden="true">
      <span className="badge-n">1</span>
      <span className="badge-bang">!</span>
    </span>
  );
}
