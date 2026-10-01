import React from 'react';
import { useLang, type TKey } from '../../lib/i18n';
import { GamachineMascot } from './GamachineMascot';
import { MascotHead } from './BrandLogo';

// TODO(profile): quest XP has no data source yet; these are the mockup's constants. Only Arena
// shows them (`.qc-xp` is hidden in the base theme).
interface Quest { id: 'explore' | 'analyze' | 'bugfix' | 'codegen'; xp: number; writes: boolean }

/**
 * The mission board. The app's starter actions were three labels on the old empty editor
 * (Bug fix / Code gen / Analyze) that did nothing when clicked; here they are cards that fill the
 * composer with a starting prompt (mockup: "a card fills the composer"), plus the mockup's
 * read-only "get to know the project". Nothing is sent until the user presses Enter.
 */
export const QUESTS: Quest[] = [
  { id: 'explore', xp: 40, writes: false },
  { id: 'analyze', xp: 60, writes: false },
  { id: 'bugfix', xp: 120, writes: true },
  { id: 'codegen', xp: 80, writes: true },
];

interface EmptyChatProps {
  userName?: string | null;
  projectName?: string | null;
  /** Puts a starting prompt into the composer. */
  onPick: (prompt: string) => void;
}

const check = <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8" /></svg>;
const shield = (
  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true">
    <path d="M10 2.8l5.6 2.2v4.4c0 3.6-2.4 6.2-5.6 7.6-3.2-1.4-5.6-4-5.6-7.6V5z" /><path d="M7.6 10l1.7 1.7 3.2-3.4" />
  </svg>
);

/** Mockup "Yeni sohbet (bos)": the mascot greets as the quest giver, the board lists starters. */
export const EmptyChat: React.FC<EmptyChatProps> = ({ userName, projectName, onPick }) => {
  const { t } = useLang();
  const name = (userName || '').trim();
  const project = (projectName || '').trim() || 'Workspace';
  return (
    <section className="empty" aria-label={t('sidebar.newChat')} data-testid="empty-chat">
      <div className="empty-hero">
        <div className="empty-figure" aria-hidden="true">
          <GamachineMascot />
          <MascotHead className="mascot empty-head" />
        </div>
        <div className="empty-say">
          <p className="empty-kicker" lang="en">Gamachine</p>
          <h1 className="empty-title">
            {name ? t('empty.title', { ad: name, proje: project }) : t('empty.titleNoName', { proje: project })}
          </h1>
          <p className="empty-sub lex">
            <span className="lex-d">{t('empty.sub')}</span>
            <span className="lex-q">{t('empty.subQuest')}</span>
          </p>
        </div>
      </div>

      <div className="empty-board">
        <p className="empty-board-k lex">
          <span className="lex-d">{t('empty.board')}</span>
          <span className="lex-q">{t('empty.boardQuest')}</span>
        </p>
        <ul className="quests">
          {QUESTS.map(q => (
            <li key={q.id}>
              <button
                type="button"
                className="qc"
                data-quest={q.id}
                onClick={() => onPick(t(`quest.${q.id}.ask` as TKey))}
              >
                <span className="qc-tag">{t(`quest.${q.id}.tag` as TKey)}</span>
                <span className="qc-title">{t(`quest.${q.id}.title` as TKey)}</span>
                <span className="qc-text">{t(`quest.${q.id}.text` as TKey)}</span>
                <span className="qc-foot">
                  {q.writes
                    ? <span className="qc-ask">{shield}{t('quest.asks')}</span>
                    : <span className="qc-safe">{check}{t('quest.safe')}</span>}
                  <span className="qc-xp num">{t('quest.xp', { xp: q.xp })}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
};
