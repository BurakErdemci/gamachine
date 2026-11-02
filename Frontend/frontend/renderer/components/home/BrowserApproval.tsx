import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { ApprovalCard, RiskyApproveButton } from './ApprovalCard';

/** The structured params of a browser_* card (backend browser branch of `/mcp-approval-request`). */
export interface BrowserCardInfo {
  tool: string;
  /** The action without the prefix: click, fill, select, press. */
  action: string;
  element: string;
  pageUrl: string;
  valuePreview: string;
  riskReason: string;
  /** browser_download only: the file, its size (null when unknown) and the site it comes from, all measured by the app. */
  filename: string;
  bytes: number | null;
  host: string;
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
    filename: str(p.filename),
    bytes: Number.isSafeInteger(p.bytes) && p.bytes >= 0 ? p.bytes : null,
    host: str(p.host),
  };
};

/** 1536 -> "1.5 KB"; whole bytes below a kilobyte. */
export const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
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
  const download = info.action === 'download';
  const site = stripBidi(info.host);
  return (
    <div className="contents" data-risky="true">
      <ApprovalCard
        kind="computer"
        testId="browser-approval"
        who={t('bp.card.who')}
        name={download ? t('bp.card.downloadName', { ad: site || t('bp.card.unknownHost') }) : t('bp.card.name', { ad: host || t('bp.card.unknownHost') })}
        sentence={t(download ? 'bp.card.downloadSentence' : 'bp.card.sentence')}
        why={(
          <p className="approval-why cu-card-risk" data-testid="bp-card-risk">
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
            <span><b>{t('cu.card.riskLabel')}</b>{reason}</span>
          </p>
        )}
        body={download ? (
          <dl className="cu-card-app" data-testid="bp-card-info">
            <dt>{t('bp.card.file')}</dt>
            <dd data-testid="bp-card-file">{stripBidi(info.filename) || t('bp.card.sizeUnknown')}</dd>
            <dt>{t('bp.card.size')}</dt>
            <dd data-testid="bp-card-size">{info.bytes === null ? t('bp.card.sizeUnknown') : formatBytes(info.bytes)}</dd>
            <dt>{t('bp.card.site')}</dt>
            <dd><code className="break-all"><b data-testid="bp-card-host">{site || t('bp.card.unknownHost')}</b></code></dd>
          </dl>
        ) : (
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
            <RiskyApproveButton testId="bp-card-approve" label={t('cu.card.approve')} onApprove={() => onAnswer(true)} />
            <button type="button" className="btn btn-ghost" data-testid="bp-card-reject" onClick={() => onAnswer(false)}>
              {t('cu.card.reject')}
            </button>
          </>
        )}
      />
    </div>
  );
};
