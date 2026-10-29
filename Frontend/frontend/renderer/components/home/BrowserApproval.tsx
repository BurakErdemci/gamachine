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
  pageUrl: string;
  valuePreview: string;
  riskReason: string;
}

const str = (v: unknown): string => (typeof v === 'string' && v.trim() ? v : '');

/** Reads the card's params; only the fields the backend computes are read, so a page cannot spoof the card. */
export const browserCardInfo = (tool: string, params: unknown): BrowserCardInfo => {
  const p: any = params && typeof params === 'object' ? params : {};
  return {
    tool,
    action: tool.replace(/^browser_/, ''),
    element: str(p.element_name),
    pageUrl: str(p.page_url),
    valuePreview: str(p.value_preview),
    riskReason: str(p.risk_reason),
  };
};

const hostOf = (url: string): string => {
  try { return new URL(url).host; } catch { return ''; }
};

// i18n.tsx is outside this change's scope, so the two new labels live here.
const LABELS = {
  tr: { value: 'Değer', badUrl: 'Sayfa adresi okunamadı; yukarıdaki metne güvenme.' },
  en: { value: 'Value', badUrl: 'The page address could not be read; do not trust the text above.' },
};

interface BrowserApprovalProps {
  info: BrowserCardInfo;
  onAnswer: (approved: boolean) => void;
  phonePaired?: boolean;
}

/** The browser_* approval card: what the agent wants to do on which page, why it is risky, and the answers. */
export const BrowserApproval: React.FC<BrowserApprovalProps> = ({ info, onAnswer, phonePaired }) => {
  const { t, lang } = useLang();
  const labels = lang === 'en' ? LABELS.en : LABELS.tr;
  const url = stripBidi(info.pageUrl);
  const host = stripBidi(hostOf(info.pageUrl));
  const at = host ? url.toLowerCase().indexOf(host.toLowerCase()) : -1;
  const element = stripBidi(info.element);
  const preview = ['fill', 'select'].includes(info.action) ? stripBidi(info.valuePreview) : '';
  const reason = stripBidi(info.riskReason) || t('bp.card.riskUnknown');
  return (
    <div className="contents" data-risky="true">
      <ApprovalCard
        kind="computer"
        testId="browser-approval"
        who={t('bp.card.who')}
        name={t('bp.card.name', { ad: host || t('bp.card.unknownHost') })}
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
            {preview && <><dt>{labels.value}</dt><dd data-testid="bp-card-value">{preview}</dd></>}
            <dt>{t('bp.card.host')}</dt>
            <dd>
              <code className="break-all" data-testid="bp-card-url">
                {at >= 0
                  ? <>{url.slice(0, at)}<b data-testid="bp-card-host">{url.slice(at, at + host.length)}</b>{url.slice(at + host.length)}</>
                  : url || t('bp.card.unknownHost')}
              </code>
              {url && !host && <p className="approval-why" data-testid="bp-card-warn">{labels.badUrl}</p>}
            </dd>
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
