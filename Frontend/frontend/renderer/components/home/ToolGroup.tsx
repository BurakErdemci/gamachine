import React from 'react';
import { ToolBlock, toolKind, toolTarget, type ToolKind } from './ToolBlock';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';

interface ToolItem { tool: string; args?: any; summary?: string; success?: boolean; output?: string; id?: string; }

const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p;
/** Only an absolute path can be opened in the workspace as is. */
const isAbsolute = (p: string) => /^([A-Za-z]:[\\/]|\/)/.test(p);

export interface ToolSummaryPart { key: string; values: Record<string, string | number>; code?: string }

/**
 * The one-line summary of a turn's steps (mockup: "3 dosya okudu · ScoreManager.cs yazdı"):
 * reads and writes are named by file when there is one, everything else is counted by kind.
 * Exported for tests; the order is the reading order of the line.
 */
export function summarizeTools(tools: ToolItem[]): ToolSummaryPart[] {
  const by: Record<ToolKind, ToolItem[]> = { read: [], wrote: [], edited: [], searched: [], ran: [], unity: [], planned: [], web: [], other: [] };
  for (const tc of tools) by[toolKind(tc.tool)].push(tc);
  const parts: ToolSummaryPart[] = [];
  const named = (list: ToolItem[], one: string, many: string) => {
    if (list.length === 0) return;
    const target = list.length === 1 ? toolTarget(list[0]) : null;
    if (target) parts.push({ key: one, values: { ad: baseName(target) }, code: baseName(target) });
    else parts.push({ key: many, values: { sayi: list.length } });
  };
  named(by.read, 'tool.sum.readOne', 'tool.sum.read');
  named([...by.wrote, ...by.edited], 'tool.sum.wrote', 'tool.sum.wroteMany');
  if (by.searched.length) parts.push({ key: 'tool.sum.searched', values: { sayi: by.searched.length } });
  if (by.ran.length) parts.push({ key: 'tool.sum.ran', values: { sayi: by.ran.length } });
  if (by.unity.length) parts.push({ key: 'tool.sum.unity', values: { sayi: by.unity.length } });
  const rest = by.planned.length + by.web.length + by.other.length;
  if (rest) parts.push({ key: 'tool.sum.other', values: { sayi: rest } });
  const failed = tools.filter(tc => tc.success === false).length;
  if (failed) parts.push({ key: 'tool.sum.failed', values: { sayi: failed } });
  return parts;
}

/**
 * A turn's tool calls as the mockup's summary chip: one line that says what happened, unfolding
 * into the step list (each step still opens its parameters and output). "Details" opens the last
 * file the turn wrote (or read) in the workspace, when its path can be opened as is.
 */
export const ToolGroup = ({ tools, onOpenFile }: { tools?: ToolItem[]; onOpenFile?: (path: string) => void }) => {
  const { t } = useLang();
  if (!tools || tools.length === 0) return null;
  const parts = summarizeTools(tools);
  const failed = tools.some(tc => tc.success === false);
  const fileTargets = tools
    .filter(tc => ['wrote', 'edited', 'read'].includes(toolKind(tc.tool)))
    .map(toolTarget)
    .filter((p): p is string => !!p && isAbsolute(p));
  const lastWritten = [...tools].reverse()
    .filter(tc => ['wrote', 'edited'].includes(toolKind(tc.tool)))
    .map(toolTarget).find((p): p is string => !!p && isAbsolute(p));
  const openTarget = lastWritten || fileTargets[fileTargets.length - 1];

  return (
    <div className="tool-row" data-testid="tool-row">
      <details className="tool fold-host" data-failed={failed || undefined}>
        <summary>
          <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 3.5a3.5 3.5 0 00-3.2 4.9L3.8 14a1.5 1.5 0 002.1 2.1l5.6-5.5a3.5 3.5 0 004.9-3.2l-2 2-2.1-.5-.5-2.1z" /></svg>
          <span className="tool-text" data-testid="tool-summary">
            {parts.map((part, i) => (
              <React.Fragment key={part.key}>
                {i > 0 && <span className="tool-sep"> · </span>}
                {part.code
                  ? renderNamed(t(part.key as any, { ...part.values, ad: '\u0000' }), stripBidi(part.code))
                  : t(part.key as any, part.values)}
              </React.Fragment>
            ))}
          </span>
          <svg className="ic ic-sm tool-chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
        </summary>
        <div className="fold"><div className="fold-in">
          <ol className="tool-steps">
            {tools.map((tc, idx) => (
              <ToolBlock key={tc.id || idx} tool={tc.tool} args={tc.args} summary={tc.summary} success={tc.success} output={tc.output} />
            ))}
          </ol>
        </div></div>
      </details>
      {openTarget && onOpenFile && (
        <button type="button" className="tool-open" onClick={() => onOpenFile(openTarget)}>
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="1.2" /><path d="M12.5 4v12" /></svg>
          {t('tool.detail')}
        </button>
      )}
    </div>
  );
};

/** "{ad} okudu" with the file name drawn as a code chip, in the sentence's own word order. */
function renderNamed(sentence: string, name: string) {
  const [before, after = ''] = sentence.split('\u0000');
  return <>{before}<code>{name}</code>{after}</>;
}
