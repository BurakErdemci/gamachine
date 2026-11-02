import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { ApprovalCard, RiskyApproveButton } from './ApprovalCard';

/** The params of an execute_blender_code card (backend `_blender_card`, tool blender_execute_code). */
export interface BlenderCardInfo {
  code: string;
  /** Length of the full code; the backend caps `code` for display. */
  codeChars: number;
  truncated: boolean;
  /** The classifier's trigger; empty in step mode for code it calls routine. */
  reason: string;
  mode: string;
}

export const BLENDER_CARD_TOOL = 'blender_execute_code';

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

export const blenderCardInfo = (params: unknown): BlenderCardInfo => {
  const p: any = params && typeof params === 'object' ? params : {};
  const code = str(p.code);
  return {
    code,
    codeChars: Number.isSafeInteger(p.code_chars) && p.code_chars >= code.length ? p.code_chars : code.length,
    truncated: p.truncated === true,
    reason: str(p.reason).trim(),
    mode: str(p.mode),
  };
};

interface BlenderApprovalProps {
  info: BlenderCardInfo;
  onAnswer: (approved: boolean) => void;
  phonePaired?: boolean;
}

/** Python the agent wants to run in the user's Blender: why it asks, the code itself, and the answers. */
export const BlenderApproval: React.FC<BlenderApprovalProps> = ({ info, onAnswer, phonePaired }) => {
  const { t } = useLang();
  const reason = stripBidi(info.reason) || t(info.mode === 'step' ? 'bl.card.stepReason' : 'bl.card.riskUnknown');
  return (
    <div className="contents" data-risky="true">
      <ApprovalCard
        kind="blender"
        testId="blender-approval"
        who={t('bl.card.who')}
        name={t('bl.card.name')}
        sentence={t('bl.card.sentence')}
        why={(
          <p className="approval-why cu-card-risk" data-testid="bl-card-risk">
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
            <span><b>{t('cu.card.riskLabel')}</b>{reason}</span>
          </p>
        )}
        body={(
          <>
            {/* Display only: the backend runs the code it holds, not this text. */}
            <pre className="approval-cmd custom-scrollbar" data-testid="bl-card-code"><code>{stripBidi(info.code)}</code></pre>
            {info.truncated && (
              <p className="approval-why" data-testid="bl-card-truncated">
                {t('bl.card.truncated', { shown: info.code.length, total: info.codeChars })}
              </p>
            )}
          </>
        )}
        phoneHint={phonePaired}
        actions={(
          <>
            <RiskyApproveButton testId="bl-card-approve" label={t('cu.card.approve')} onApprove={() => onAnswer(true)} />
            <button type="button" className="btn btn-ghost" data-testid="bl-card-reject" onClick={() => onAnswer(false)}>
              {t('cu.card.reject')}
            </button>
          </>
        )}
      />
    </div>
  );
};
