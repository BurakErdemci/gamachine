import React from 'react';
import { Info } from 'lucide-react';
import { useLang } from '../../lib/i18n';

/**
 * Shown while the open C# file is outside every csproj. OmniSharp then reports
 * syntax errors only (measured 28 Sep 2026), and an empty problems list would
 * otherwise read as "this file is fine".
 */
export function CsharpProjectHint({ inProject }: { inProject: boolean | null }) {
  const { t } = useLang();
  if (inProject !== false) return null;
  return (
    // The Kod pane's note line (workspace.css `.ws-hint`), on the paper tokens.
    <div role="status" className="ws-hint flex items-start gap-2">
      <Info size={13} className="mt-[2px] shrink-0" aria-hidden="true" />
      <span>{t('editor.csharpNotInProject')}</span>
    </div>
  );
}
