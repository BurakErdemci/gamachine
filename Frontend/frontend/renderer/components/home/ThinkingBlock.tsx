import { useLang } from '../../lib/i18n';

interface ThinkingBlockProps {
  thinking: string;
  durationMs?: number | null;
}

/** "Thought for 12s": the same folding chip as the tool summary (mockup `.tool`), opening the reasoning. */
export const ThinkingBlock = ({ thinking, durationMs }: ThinkingBlockProps) => {
  const { t } = useLang();
  const seconds = durationMs ? Math.round(durationMs / 1000) : null;
  const label = seconds ? t('thinking.seconds', { sayi: seconds }) : t('thinking.done');

  return (
    <div className="tool-row">
      <details className="tool tool-thinking fold-host">
        <summary>
          <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 6v4.3l2.8 1.8" /></svg>
          <span className="tool-text">{label}</span>
          <svg className="ic ic-sm tool-chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
        </summary>
        <div className="fold"><div className="fold-in">
          <p className="thinking-text custom-scrollbar">{thinking}</p>
        </div></div>
      </details>
    </div>
  );
};
