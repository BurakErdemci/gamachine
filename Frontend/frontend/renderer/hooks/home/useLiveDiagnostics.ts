import { useEffect, useState } from 'react';
import axios from 'axios';
import { workspaceRelativePath } from '../../components/home/EditorPanel';

export const CHANGE_DEBOUNCE_MS = 700;
// The backend waits ~1.2 s for OmniSharp's answer. Cold start measured 28 Sep
// 2026: empty at 1.34 s, the two errors 60 ms later, so the editor showed
// nothing until the next keystroke. A csproj reload after
// didChangeWatchedFiles took 2.5 s; the second ask covers that.
export const LATE_DIAGNOSTICS_MS = [1500, 4000];

export interface LspStatus { state: string; detail: string }

interface Options {
  API: string | null | undefined;
  sessionToken: string | null | undefined;
  openedFilePath: string | null;
  workspacePath: string | null;
  code: string;
  setProjectProblems: (update: (prev: Record<string, any[]>) => Record<string, any[]>) => void;
}

const isCSharp = (p: string | null) => !!p && p.toLowerCase().endsWith('.cs');

/**
 * Live C# diagnostics for the open file: `/lsp/change` after a pause in typing,
 * then `/lsp/diagnostics` re-asks for what OmniSharp published later. Only
 * `.cs` files produce traffic.
 */
export function useLiveDiagnostics({
  API, sessionToken, openedFilePath, workspacePath, code, setProjectProblems,
}: Options) {
  const [lspStatus, setLspStatus] = useState<LspStatus | null>(null);
  // false = OmniSharp runs and no csproj compiles this file: syntax errors only.
  const [inProject, setInProject] = useState<boolean | null>(null);

  useEffect(() => { setInProject(null); }, [openedFilePath]);

  useEffect(() => {
    if (!API || !sessionToken || !isCSharp(openedFilePath)) return;
    const headers = { 'X-Session-Token': sessionToken };
    const relativeFile = workspaceRelativePath(openedFilePath!, workspacePath);
    const timers: ReturnType<typeof setTimeout>[] = [];
    let cancelled = false;

    const apply = (data: any, current: boolean) => {
      if (data?.problems) setProjectProblems(prev => ({ ...prev, [relativeFile]: data.problems }));
      setLspStatus(data?.status || null);
      if (current) setInProject(typeof data?.inProject === 'boolean' ? data.inProject : null);
    };
    const ask = async () => {
      try {
        const res = await axios.get(`${API}/lsp/diagnostics`, { params: { path: relativeFile }, headers });
        if (!cancelled) apply(res.data, true);
      } catch { /* sidecar kapalıysa sessiz */ }
    };
    const askAfter = (delays: number[]) => {
      for (const ms of delays) timers.push(setTimeout(ask, ms));
    };

    timers.push(setTimeout(async () => {
      try {
        const res = await axios.post(`${API}/lsp/change`, { path: relativeFile, text: code }, { headers });
        // Problems are applied even when a newer keystroke is pending, as
        // before this hook existed: a fast typist would otherwise see none.
        apply(res.data, !cancelled);
        if (!cancelled) askAfter(LATE_DIAGNOSTICS_MS);
      } catch { /* sidecar kapalıysa sessiz */ }
    }, CHANGE_DEBOUNCE_MS));

    // Coming back from Unity is when it has usually regenerated the csproj;
    // each ask lets the backend notice and OmniSharp reload.
    const onFocus = () => askAfter([0, ...LATE_DIAGNOSTICS_MS]);
    window.addEventListener('focus', onFocus);

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
      window.removeEventListener('focus', onFocus);
    };
  }, [code, openedFilePath, workspacePath, API, sessionToken]);

  return { lspStatus, inProject };
}
