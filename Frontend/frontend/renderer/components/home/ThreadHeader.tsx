import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import type { Conversation } from './types';

interface ThreadHeaderProps {
  conversation: Conversation | null;
  projectName?: string | null;
  /** An approval card is waiting in this chat right now. */
  awaiting: boolean;
  /** The chat's own tools (side question, branch), kept from the old chat header. */
  actions?: React.ReactNode;
}

/** dd.mm.yy, the mockup's date cell; nothing when the stored date does not parse. */
export const shortDate = (iso?: string | null): string => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${String(d.getFullYear()).slice(-2)}`;
};

/**
 * The chat's title block (mockup `.title-block`): task number, subject, scene, approvals, date.
 * Pafta draws it as the boxed cell row of a technical drawing; the other themes as one line.
 *
 * Two cells differ from the mockup because the app has no source for them yet: the "Scene" cell
 * shows the open project (the active Unity scene is not reported to the renderer), and the
 * "Approval" cell shows a waiting request instead of a decided/total count (decisions are not
 * counted per chat). Each cell is left out rather than shown with a made-up value.
 */
export const ThreadHeader: React.FC<ThreadHeaderProps> = ({ conversation, projectName, awaiting, actions }) => {
  const { t } = useLang();
  if (!conversation) return null;
  const date = shortDate(conversation.created_at);
  return (
    <header className="title-block" aria-label={t('tb.subject')} data-testid="thread-header">
      <div className="tb-cell tb-no">
        <span className="tb-k lex">
          <span className="lex-d">{t('tb.no')}</span>
          <span className="lex-q">{t('tb.noQuest')}</span>
          <span className="lex-p">{t('tb.noSheet')}</span>
          <span className="lex-w">{t('tb.noOrder')}</span>
        </span>
        <span className="tb-v">{conversation.id}</span>
      </div>
      <div className="tb-cell tb-title">
        <span className="tb-k">{t('tb.subject')}</span>
        <span className="tb-v">{stripBidi(conversation.title || '')}</span>
      </div>
      {projectName && (
        <div className="tb-cell tb-model tb-scene">
          <span className="tb-k">{t('tb.project')}</span>
          <span className="tb-v">{projectName}</span>
        </div>
      )}
      {awaiting && (
        <div className="tb-cell tb-rev tb-step" data-testid="thread-header-waiting">
          <span className="tb-k">{t('tb.approval')}</span>
          <span className="tb-v">{t('tb.waiting')}</span>
        </div>
      )}
      {date && (
        <div className="tb-cell tb-date">
          <span className="tb-k">{t('tb.date')}</span>
          <span className="tb-v">{date}</span>
        </div>
      )}
      {actions && <div className="tb-actions" role="group" aria-label={t('tb.actions')}>{actions}</div>}
    </header>
  );
};
