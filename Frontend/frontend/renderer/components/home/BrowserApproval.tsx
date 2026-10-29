import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { ApprovalCard, CheckIcon } from './ApprovalCard';

/** The structured params of a browser_* card (backend browser branch of `/mcp-approval-request`). */
export interface BrowserCardInfo {
  tool: string;
  /** The action without the prefix: click, fill, select, press. */
  action: string;
  element: string;
  host: string;
  riskReason: string;
}

const str = (v: unknown): string => (typeof v === 'string' && v.trim() ? v : '');

const hostOf = (url: string): string => {
  try { return new URL(url).host; } catch { return ''; }
};

/** Reads the card's params; anything malformed falls back to empty, never throws. */
export const browserCardInfo = (tool: string, params: unknown): BrowserCardInfo => {
  const p: any = params && typeof params === 'object' ? params : {};
  return {
    tool,
    action: tool.replace(/^browser_/, ''),
    element: str(p.element_name) || str(p.element) || str(p.name),
    host: str(p.host) || str(p.page_host) || hostOf(str(p.url)),
    riskReason: str(p.risk_reason),
  };
};

interface BrowserApprovalProps {
  info: BrowserCardInfo;
  onAnswer: (approved: boolean) => void;
  phonePaired?: boolean;
}

/** The browser_* approval card: what the agent wants to do on which page, why it is risky, and the answers. */
export const BrowserApproval: React.FC<BrowserApprovalProps> = ({ info, onAnswer, phonePaired }) => {
  const { t } = useLang();
  const host = stripBidi(info.host) || t('bp.card.unknownHost');
  const element = stripBidi(info.element);
  const reason = stripBidi(info.riskReason) || t('bp.card.riskUnknown');
  return (
    <div className="contents" data-risky="true">
      <ApprovalCard
        kind="computer"
        testId="browser-approval"
        who={t('bp.card.who')}
        name={t('bp.card.name', { ad: host })}
        sentence={t('bp.card.sentence')}
        why={(
          <p className="approval-why cu-card-risk" data-testid="bp-card-risk">
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
            <span><b>{t('cu.card.riskLabel')}</b>{reason}</span>
          </p>
        )}
        body={(
          <dl className="cu-card-app" data-testid="bp-card-info">
            <dt>{t('bp.card.action')}</dt>
            <dd><code>{stripBidi(info.action)}</code></dd>
            {element && <><dt>{t('bp.card.element')}</dt><dd data-testid="bp-card-element">{element}</dd></>}
            <dt>{t('bp.card.host')}</dt>
            <dd data-testid="bp-card-host">{host}</dd>
          </dl>
        )}
        phoneHint={phonePaired}
        actions={(
          <>
            <button type="button" className="btn btn-primary" data-testid="bp-card-approve" onClick={() => onAnswer(true)}>
              <CheckIcon /><span>{t('cu.card.approve')}</span>
            </button>
            <button type="button" className="btn btn-ghost" data-testid="bp-card-reject" onClick={() => onAnswer(false)}>
              {t('cu.card.reject')}
            </button>
          </>
        )}
      />
    </div>
  );
};
