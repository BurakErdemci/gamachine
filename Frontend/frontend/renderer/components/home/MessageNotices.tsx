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


/** What Continue answered: released, still running (409 while a turn runs), or failed. */
export type ContinueAnswer = 'ok' | 'running' | 'failed';

/**
 * Helper results the backend is holding, with Continue (INFO_DESIGN.md C3, D5). Two reasons:
 * - the fuse: ten automatic turns in a row, so the chat waits for the user (`tripped`);
 * - a Stop: results that arrived after it wait instead of starting turns (`paused`, audit F3).
 * Shown whenever results are held. The one saturated element in the thread, because it waits for
 * you: one sentence and one button. Writing a message steers instead and takes them along.
 */
export const FuseNotice: React.FC<{
  onContinue: () => Promise<ContinueAnswer | boolean>;
  /** The fuse tripped; otherwise results wait after a Stop. */
  tripped?: boolean;
  /** How many results are held (the Stop wording counts them). */
  held?: number;
}> = ({ onContinue, tripped = true, held = 0 }) => {
  const { t } = useLang();
  const [busy, setBusy] = React.useState(false);
  const [answer, setAnswer] = React.useState<ContinueAnswer | null>(null);
  const go = async () => {
    setBusy(true);
    setAnswer(null);
    const got = await onContinue().catch(() => 'failed' as const);
    setBusy(false);
    setAnswer(got === true ? 'ok' : got === false ? 'failed' : got);
  };
  const hint = answer === 'running' ? t('helper.continueRunning')
    : answer === 'failed' ? t('helper.fuseFailed')
    : tripped ? t('helper.fuseHint') : t('helper.heldHint');
  return (
    <div className="notices">
      <div className={`notice is-fuse${tripped ? '' : ' is-held'}`} data-kind={tripped ? 'fuse' : 'held'} data-testid="fuse-notice" role="status">
        <div className="notice-row">
          <AlertTriangle size={14} className="notice-ic" aria-hidden="true" />
          <div className="min-w-0">
            <div className="notice-k">
              {tripped ? t('helper.fuseTitle') : held === 1 ? t('helper.heldOne') : t('helper.held', { n: held })}
            </div>
            <div className="notice-msg">{tripped ? t('helper.fuseMsg') : t('helper.heldMsg')}</div>
            <div className="fuse-acts">
              <button type="button" className="btn btn-primary btn-sm" data-testid="fuse-continue" disabled={busy} onClick={() => void go()}>
                {t('helper.fuseContinue')}
              </button>
              <span className="hint" data-testid="fuse-hint">{hint}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
