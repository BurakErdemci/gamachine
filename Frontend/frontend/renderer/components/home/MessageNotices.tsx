import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { MessageNotice } from './types';

/**
 * Non-error notices under an assistant message: a run that hit its iteration
 * cap, or a side pipeline (video download/extract) that broke while the run
 * itself carried on.
 *
 * Own component rather than inline JSX in `ChatPanel` so it can be rendered in a
 * test without dragging in Monaco through `DiffViewer` — "the function was
 * called" is not "the user saw it", and this repo has already paid for that
 * (`ToastContainer` was defined, exported and mounted nowhere).
 *
 * Not red and no ❌: that look is reserved for a turn that actually failed.
 * A capped run did real work, so dressing it as a crash sends the user looking
 * for a bug that is not there.
 */
export const MessageNotices: React.FC<{ notices?: MessageNotice[] }> = ({ notices }) => {
  const { t } = useLang();
  if (!notices || notices.length === 0) return null;
  return (
    <div className="notices">
      {notices.map((notice, i) => (
        <div key={i} className="notice" data-kind={notice.kind}>
          <div className="notice-row">
            <AlertTriangle size={14} className="notice-ic" aria-hidden="true" />
            <div className="min-w-0">
              <div className="notice-k">{notice.title}</div>
              {/* break-words: the message can carry one unbroken token (a URL,
                  a path) long enough to push the chat column sideways. */}
              <div className="notice-msg">{stripBidi(notice.message)}</div>
              {notice.detail && (
                <details className="notice-detail">
                  <summary>
                    {t('notice.detail')}
                  </summary>
                  <pre>{stripBidi(notice.detail)}</pre>
                </details>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

/**
 * The fuse (INFO_DESIGN.md C3, D5): after ten automatic turns in a row the backend holds the chat
 * and waits for the user. The one saturated element in the thread, because it waits for you:
 * one sentence and one button. Continue allows ten more; writing a message steers instead.
 */
export const FuseNotice: React.FC<{ onContinue: () => Promise<boolean> }> = ({ onContinue }) => {
  const { t } = useLang();
  const [busy, setBusy] = React.useState(false);
  const [failed, setFailed] = React.useState(false);
  const go = async () => {
    setBusy(true);
    setFailed(false);
    const ok = await onContinue().catch(() => false);
    setBusy(false);
    if (!ok) setFailed(true);
  };
  return (
    <div className="notices">
      <div className="notice is-fuse" data-kind="fuse" data-testid="fuse-notice" role="status">
        <div className="notice-row">
          <AlertTriangle size={14} className="notice-ic" aria-hidden="true" />
          <div className="min-w-0">
            <div className="notice-k">{t('helper.fuseTitle')}</div>
            <div className="notice-msg">{t('helper.fuseMsg')}</div>
            <div className="fuse-acts">
              <button type="button" className="btn btn-primary btn-sm" data-testid="fuse-continue" disabled={busy} onClick={() => void go()}>
                {t('helper.fuseContinue')}
              </button>
              <span className="hint">{failed ? t('helper.fuseFailed') : t('helper.fuseHint')}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
