import React, { useEffect, useState } from 'react';
import { useLang } from '../../lib/i18n';

export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'failed';

interface ApprovalCardProps {
  state?: ApprovalState;
  /** What kind of request (shell / unity / mail / delete / create / question); for styling and tests. */
  kind: string;
  /** Header label; defaults to "Approval needed" while pending. */
  title?: React.ReactNode;
  /** Header source cell (mockup: "Unity · SampleScene"). */
  who?: React.ReactNode;
  /** The request in plain words (h3). */
  name: React.ReactNode;
  sentence?: React.ReactNode;
  /** The `.approval-why` line: the risk line, or the card's own warning. */
  why?: React.ReactNode;
  /** Always-visible content (the command being approved, the file list, the questions). */
  body?: React.ReactNode;
  /** Folded raw detail behind "Details". */
  detail?: React.ReactNode;
  /** The decision buttons (`.btn-primary` first). Hidden by the CSS once decided. */
  actions?: React.ReactNode;
  /** A paired phone can decide this card too. */
  phoneHint?: boolean;
  /** The decided record line. */
  result?: React.ReactNode;
  /** A control that stays usable after the decision (e.g. "Close" on the done card). */
  dismiss?: React.ReactNode;
  testId?: string;
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

/**
 * The mockup's approval card (`section.approval`, "quest window structure"):
 * strip (mark, kicker, title, wait counter, source) / name / sentence / risk line / detail /
 * actions / result / stamp. One DOM for every theme: Arena draws a chamfered quest window, Pafta
 * a revision request on the title block, Atolye a work-order tag, Sade a plain card. The
 * decision logic stays in the components that use it; this only draws.
 */
export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  state = 'pending', kind, title, who, name, sentence, why, body, detail, actions, phoneHint, result, dismiss, testId,
}) => {
  const { t } = useLang();
  const pending = state === 'pending';
  // "waiting 0:12": shown where motion is reduced and in Sade, which says the wait in words
  // instead of nudging (KARAKTER 12); counted here so the number is real.
  const [waited, setWaited] = useState(0);
  // #6 onay-gelis: the card arrives once (its edge drawn left to right), then the class goes, so
  // the arrival line is not left standing on the waiting card.
  const [arriving, setArriving] = useState(pending);
  useEffect(() => {
    if (!arriving) return;
    const id = setTimeout(() => setArriving(false), 600);
    return () => clearTimeout(id);
  }, [arriving]);
  useEffect(() => {
    if (!pending) return;
    const start = Date.now();
    const id = setInterval(() => setWaited(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, [pending]);

  return (
    <section
      className={`approval${pending ? ' is-waiting' : ''}${pending && arriving ? ' is-arriving' : ''}`}
      data-state={state}
      data-kind={kind}
      data-testid={testId}
      aria-label={pending ? t('card.needed') : undefined}
    >
      <span className="approval-corner" aria-hidden="true" />
      <span className="approval-line" aria-hidden="true" />
      <header className="approval-head">
        <span className="approval-mark" aria-hidden="true" />
        <span className="approval-kicker lex">
          <span className="lex-d">{t('card.kicker')}</span>
          <span className="lex-q">{t('card.kickerQuest')}</span>
          <span className="lex-p">{t('card.kickerSheet')}</span>
          <span className="lex-w">{t('card.kickerOrder')}</span>
        </span>
        <span className="approval-title">{title ?? t('card.needed')}</span>
        {pending && (
          <span className="approval-wait" aria-live="off">
            <svg className="ic aw-ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 6v4.3l2.8 1.8" /></svg>
            <span className="aw-word">{t('card.waiting')} </span><span className="num">{fmt(waited)}</span>
          </span>
        )}
        {who && <span className="approval-who">{who}</span>}
      </header>
      <h3 className="approval-name">{name}</h3>
      {sentence && <p className="approval-sentence">{sentence}</p>}
      {why}
      {body && <div className="approval-body">{body}</div>}
      {detail && (
        <details className="approval-detail fold-host">
          <summary>{t('card.detail')}</summary>
          <div className="fold"><div className="fold-in">{detail}</div></div>
        </details>
      )}
      {actions && (
        <div className="approval-actions">
          {actions}
          {phoneHint && pending && <span className="approval-hint">{t('card.phoneHint')}</span>}
        </div>
      )}
      {result && <p className="approval-result" role="status">{result}</p>}
      {dismiss && <div className="approval-dismiss">{dismiss}</div>}
      <span className="strike" aria-hidden="true" />
      <span className="stamp" aria-hidden="true">
        <span className="stamp-ok">
          <span className="lex-d">{t('card.stampApproved')}</span>
          <span className="lex-q">{t('card.stampDone')}</span>
        </span>
        <span className="stamp-no">{t('card.stampRejected')}</span>
      </span>
    </section>
  );
};

/** The check icon the primary decision button carries (mockup `#i-check`). */
export const CheckIcon = () => (
  <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8" /></svg>
);

/**
 * A card's own warning line, drawn where the risk line goes (`.approval-why`). `secondary` is the
 * same warning drawn UNDER a balanced-mode risk line: the risk line says why the card stopped
 * here, the warning still says what the action does, so a card with a reason shows both.
 */
export const ApprovalWhy: React.FC<{ children: React.ReactNode; secondary?: boolean }> = ({ children, secondary }) => (
  <p className={secondary ? 'approval-why is-note' : 'approval-why'} data-testid="card-warning">
    <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
    <span>{children}</span>
  </p>
);
