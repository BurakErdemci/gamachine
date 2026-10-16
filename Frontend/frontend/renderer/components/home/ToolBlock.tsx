import { useState } from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { describeComputerArgs, describeComputerOutput, userStopped } from '../../lib/computerStep';

interface ToolBlockProps {
  tool: string;
  args?: any;
  summary?: string;
  success?: boolean;
  output?: string;
}

export type ToolKind = 'read' | 'wrote' | 'edited' | 'searched' | 'ran' | 'unity' | 'computer' | 'planned' | 'web' | 'other';

// Claude Code built-ins plus the old MCP names still found in stored chats.
const KIND: Record<string, ToolKind> = {
  Read: 'read', read_file: 'read', NotebookRead: 'read',
  Write: 'wrote', write_file: 'wrote',
  Edit: 'edited', MultiEdit: 'edited', NotebookEdit: 'edited',
  Glob: 'searched', Grep: 'searched', search_in_project: 'searched', find_files: 'searched', list_directory: 'searched',
  Bash: 'ran',
  TodoWrite: 'planned',
  WebSearch: 'web', WebFetch: 'web',
};

export const toolKind = (tool: string): ToolKind =>
  KIND[tool] ?? (tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_') ? 'computer' : tool.startsWith('mcp__') ? 'unity' : 'other');

/** The file or thing a step acted on: its path argument first, then the stream's summary. */
export const toolTarget = (tc: { args?: any; summary?: string }): string | null => {
  const a = tc.args && typeof tc.args === 'object' ? tc.args : null;
  for (const k of ['file_path', 'path', 'notebook_path']) {
    if (a && typeof a[k] === 'string' && a[k]) return a[k];
  }
  return null;
};

/**
 * One step of a turn (mockup `.tool-steps li`): the verb, then what it acted on. A click opens
 * the recorded parameters and output, as the old chip did.
 */
export const ToolBlock = ({ tool, args, summary, success, output }: ToolBlockProps) => {
  const [open, setOpen] = useState(false);
  const { t } = useLang();
  const kind = toolKind(tool);
  const isSuccess = success ?? true;
  const stopped = success === false && userStopped(tool, output);
  const unityName = tool.replace('mcp__unityMCP__', '').replace('mcp__', '');
  const target = toolTarget({ args, summary }) || summary || (kind === 'unity' ? unityName : tool);
  const hasArgs = args != null && (typeof args !== 'object' || Object.keys(args).length > 0);
  const hasOutput = !!(output && output.trim());
  const computerArgs = kind === 'computer' && hasArgs ? describeComputerArgs(tool, args) : null;
  const computerOutput = kind === 'computer' && hasOutput ? describeComputerOutput(output) : null;
  const rawBlocks = open && <>
    {hasArgs && (
      <div>
        <p className="tool-out-k">{t('tool.params')}</p>
        <pre>{JSON.stringify(args, null, 2)}</pre>
      </div>
    )}
    {hasOutput && (
      <div>
        <p className="tool-out-k">{t('tool.output')}</p>
        <pre>{output}</pre>
      </div>
    )}
  </>;

  return (
    <li className="tool-step" data-failed={(!isSuccess && !stopped) || undefined} data-stopped={stopped || undefined}>
      <button type="button" className="tool-step-btn" aria-expanded={open} onClick={() => setOpen(v => !v)}>
        <span className="tool-verb">{t(`tool.verb.${kind}` as any)}</span>
        <code className="tool-target">{stripBidi(String(target))}</code>
        {stopped
          ? <span className="tool-stopped">{t('tool.verb.stopped')}</span>
          : !isSuccess && <span className="tool-fail">{t('tool.verb.failed')}</span>}
      </button>
      {open && (
        <div className="tool-out custom-scrollbar">
          {!hasArgs && !hasOutput ? (
            <p className="tool-out-k">{t('tool.noDetail')}</p>
          ) : kind === 'computer' ? (
            <>
              {computerArgs && (
                <div>
                  <p className="tool-out-k">{t('tool.params')}</p>
                  <p className="tool-cu-meta">{stripBidi(computerArgs.meta.join(' · '))}</p>
                  {!!computerArgs.steps.length && (
                    <ol className="tool-cu-steps">
                      {computerArgs.steps.map((step, index) => <li key={index}>{stripBidi(step)}</li>)}
                    </ol>
                  )}
                </div>
              )}
              {computerOutput && (
                <div>
                  <p className="tool-out-k">{t('tool.output')}</p>
                  <p className="tool-cu-result">
                    {t('tool.cu.ran', { n: computerOutput.executed, t: computerOutput.total })}
                    {computerOutput.stopped ? ' · ' + t('tool.cu.stopped')
                      : computerOutput.failedStep != null ? ' · ' + t('tool.cu.failedAt', { n: computerOutput.failedStep + 1 }) : ''}
                  </p>
                  {!!computerOutput.steps.length && (
                    <ol className="tool-cu-steps">
                      {computerOutput.steps.map((step, index) => (
                        <li key={index} data-ok={step.ok}>{step.ok ? '✓ ' : '✗ '}{stripBidi(step.message)}</li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
              <details className="tool-raw">
                <summary>{t('tool.raw')}</summary>
                {rawBlocks}
              </details>
            </>
          ) : rawBlocks}
        </div>
      )}
    </li>
  );
};
