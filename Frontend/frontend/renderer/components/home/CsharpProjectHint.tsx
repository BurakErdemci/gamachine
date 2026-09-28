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
    <div
      role="status"
      className="flex items-start gap-2 px-4 py-1.5 border-b border-amber-500/20 bg-amber-500/[0.06] text-[11px] leading-snug text-amber-200/90 shrink-0"
    >
      <Info size={13} className="mt-[1px] shrink-0 text-amber-400" />
      <span>{t('editor.csharpNotInProject')}</span>
    </div>
  );
}
