import React from 'react';
import { useLang, translations, type Lang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';

/** Label for a backend risk code; an unknown code is shown as sent, never hidden. */
export const riskReasonLabel = (lang: Lang, code: string): string => {
  const table = translations[lang];
  const key = `risk.${code}`;
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : code;
};

interface RiskReasonLineProps {
  reason?: string | null;
  detail?: string | null;
  className?: string;
}

/**
 * "Critical: <why>" on an approval card that balanced mode raised, drawn as the mockup's risk
 * line (`.approval-why` with `.risk` bars). Every reason balanced mode raises is a critical one,
 * so the bars read level 3 of 3. Renders nothing without a reason, so cards from auto/step look
 * exactly as before. The test id sits on the sentence alone: the bars are decoration.
 */
export const RiskReasonLine: React.FC<RiskReasonLineProps> = ({ reason, detail, className = '' }) => {
  const { lang, t } = useLang();
  if (!reason) return null;
  // The detail echoes model-controlled input (a path, a program name), so it
  // gets the same bidi cleanup as the command itself.
  const shownDetail = detail ? stripBidi(detail) : '';
  return (
    <p className={`approval-why ${className}`.trim()}>
      <span className="risk" data-level="3" role="img" aria-label={t('card.riskHigh')}>
        <span className="risk-k" aria-hidden="true">{t('card.risk')}</span><i /><i /><i />
      </span>
      <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
      <span data-testid="risk-reason" className="risk-text">
        <b>{t('risk.label')}</b>{' '}
        {stripBidi(riskReasonLabel(lang, reason))}
        {shownDetail && <span className="risk-detail"> — {shownDetail}</span>}
      </span>
    </p>
  );
};
