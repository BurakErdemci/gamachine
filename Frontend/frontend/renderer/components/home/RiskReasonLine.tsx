import React from 'react';
import { ShieldAlert } from 'lucide-react';
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
 * "Critical: <why>" on an approval card that balanced mode raised. Renders
 * nothing without a reason, so cards from auto/step look exactly as before.
 */
export const RiskReasonLine: React.FC<RiskReasonLineProps> = ({ reason, detail, className = '' }) => {
  const { lang, t } = useLang();
  if (!reason) return null;
  // The detail echoes model-controlled input (a path, a program name), so it
  // gets the same bidi cleanup as the command itself.
  const shownDetail = detail ? stripBidi(detail) : '';
  return (
    <div
      data-testid="risk-reason"
      className={`flex items-start gap-1.5 text-[11px] leading-snug text-amber-300 ${className}`}
    >
      <ShieldAlert size={12} className="mt-px shrink-0" />
      <span className="min-w-0 break-words">
        <span className="font-bold">{t('risk.label')}</span>{' '}
        {stripBidi(riskReasonLabel(lang, reason))}
        {shownDetail && <span className="font-mono text-amber-200/80"> — {shownDetail}</span>}
      </span>
    </div>
  );
};
