import React from 'react';
import { useLang } from '../../lib/i18n';
import { ApprovalCard, ApprovalWhy, CheckIcon } from './ApprovalCard';
import { RiskReasonLine } from './RiskReasonLine';

interface FileDeleteApprovalProps {
  path: string;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
  /** Why balanced mode stopped here; absent for auto/step cards. */
  riskReason?: string;
  riskDetail?: string;
  /** A paired phone can decide this card too. */
  phonePaired?: boolean;
}

/** "Delete <file>?" on the mockup's approval card; the path stays visible in full. */
export const FileDeleteApproval: React.FC<FileDeleteApprovalProps> = ({
  path,
  onConfirm,
  onCancel,
  riskReason,
  riskDetail,
  phonePaired,
}) => {
  const { t } = useLang();
  const fileName = path.split('/').pop() || path;

  return (
    <ApprovalCard
      kind="delete"
      testId="delete-approval"
      title={t('deleteApproval.title')}
      who={t('card.whoFile')}
      name={t('card.nameDelete', { ad: fileName })}
      sentence={<>"<code>{fileName}</code>" {t('deleteApproval.confirm')}</>}
      why={riskReason ? <RiskReasonLine reason={riskReason} detail={riskDetail} /> : <ApprovalWhy>{t('deleteApproval.warning')}</ApprovalWhy>}
      body={<p className="approval-path">{path}</p>}
      phoneHint={phonePaired}
      actions={(
        <>
          <button type="button" onClick={onConfirm} className="btn btn-primary">
            <CheckIcon />
            <span>{t('deleteApproval.yes')}</span>
          </button>
          <button type="button" onClick={onCancel} className="btn btn-ghost">
            {t('deleteApproval.cancel')}
          </button>
        </>
      )}
    />
  );
};
