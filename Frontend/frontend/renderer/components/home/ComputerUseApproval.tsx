import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { isCodeEditorBundle } from '../../lib/computerUse';
import { ApprovalCard, ApprovalWhy, CheckIcon, RiskyApproveButton } from './ApprovalCard';

/** The structured params of a computer_* card (backend computer branch of `/mcp-approval-request`). */
export interface ComputerCardInfo {
  tool: string;
  bundleId: string | null;
  appName: string | null;
  pid: number | null;
  risky: boolean;
  /** The model's / policy's reason for a risky step (free text, may be English). */
  riskReason: string;
  summary: string;
  foreground: boolean;
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

/** Reads the card's params; anything malformed falls back to "unknown", never throws. */
export const computerCardInfo = (tool: string, params: unknown, entry?: unknown): ComputerCardInfo => {
  const p: any = params && typeof params === 'object' ? params : {};
  const e: any = entry && typeof entry === 'object' ? entry : {};
  return {
    tool,
    bundleId: str(p.bundle_id),
    appName: str(p.app_name),
    pid: typeof p.pid === 'number' && Number.isInteger(p.pid) ? p.pid : null,
    risky: tool === 'computer_confirm_risky_step' || p.risky === true,
    riskReason: str(p.risk_reason) ?? '',
    summary: str(p.summary) ?? str(e.summary) ?? '',
    foreground: p.foreground === true,
  };
};

/** Is this the "risky step" card (Approve / Deny only) rather than the first-use card? */
export const isRiskyComputerCard = (info: ComputerCardInfo) => info.risky;

interface ComputerUseApprovalProps {
  info: ComputerCardInfo;
  /** `remember` = "Always" for this app; only the first-use card offers it. */
  onAnswer: (approved: boolean, remember: boolean) => void;
  phonePaired?: boolean;
}

/**
 * The computer_* approval card: which app, what will happen, and the answers. Built on the
 * shared `ApprovalCard` chrome so every theme draws it as it draws every other card.
 *
 * First use of an app: "This time" / "Always" / "Deny". A risky final step: "Approve" /
 * "Deny" only, with the reason up front; an approved risky step never becomes a standing grant
 * (backend refuses it as well).
 */
export const ComputerUseApproval: React.FC<ComputerUseApprovalProps> = ({ info, onAnswer, phonePaired }) => {
  const { t } = useLang();
  const risky = isRiskyComputerCard(info);
  const app = stripBidi(info.appName ?? info.bundleId ?? t('cu.card.unknownApp'));
  const editor = isCodeEditorBundle(info.bundleId);
  const summary = stripBidi(info.summary);

  const why = risky ? (
    <p className="approval-why cu-card-risk" data-testid="cu-card-risk">
      <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
      <span><b>{t('cu.card.riskLabel')}</b>{stripBidi(info.riskReason) || t('cu.card.riskUnknown')}</span>
    </p>
  ) : <ApprovalWhy>{t('cu.card.whyFirst')}</ApprovalWhy>;

  return (
    <div className="contents" data-risky={risky ? 'true' : 'false'}>
      <ApprovalCard
        kind="computer"
        testId="computer-approval"
        who={t('cu.card.who')}
        name={t(risky ? 'cu.card.nameRisky' : 'cu.card.nameFirst', { ad: app })}
        sentence={t(risky ? 'cu.card.sentenceRisky' : 'cu.card.sentenceFirst')}
        why={why}
        body={(
          <>
            {summary && <p className="cu-card-summary" data-testid="cu-card-summary">{summary}</p>}
            <dl className="cu-card-app" data-testid="cu-card-app">
              <dt>{t('cu.card.app')}</dt>
              <dd>{app}{info.bundleId && info.appName ? <> <span className="set-key">{stripBidi(info.bundleId)}</span></> : null}</dd>
              <dt>{t('cu.card.tool')}</dt>
              <dd><code>{stripBidi(info.tool)}</code></dd>
            </dl>
            {editor && (
              <p className="cu-card-note" data-note="editor" data-testid="cu-card-editor">
                <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6l4 4-4 4M10 14h6" /></svg>
                <span>{t('cu.card.editor')}</span>
              </p>
            )}
            {info.foreground && (
              <p className="cu-card-note" data-note="foreground" data-testid="cu-card-foreground">
                <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="10" rx="1" /><path d="M7 17h6" /></svg>
                <span>{t('cu.card.foreground')}</span>
              </p>
            )}
          </>
        )}
        phoneHint={phonePaired}
        actions={risky ? (
          <>
            <RiskyApproveButton testId="cu-card-approve" label={t('cu.card.approve')} onApprove={() => onAnswer(true, false)} />
            <button type="button" className="btn btn-ghost" data-testid="cu-card-reject" onClick={() => onAnswer(false, false)}>
              {t('cu.card.reject')}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-primary" data-testid="cu-card-once" onClick={() => onAnswer(true, false)}>
              <CheckIcon /><span>{t('cu.card.once')}</span>
            </button>
            <button type="button" className="btn btn-ghost" data-testid="cu-card-always" onClick={() => onAnswer(true, true)}>
              {t('cu.card.always')}
            </button>
            <button type="button" className="btn btn-ghost" data-testid="cu-card-reject" onClick={() => onAnswer(false, false)}>
              {t('cu.card.reject')}
            </button>
          </>
        )}
      />
    </div>
  );
};
