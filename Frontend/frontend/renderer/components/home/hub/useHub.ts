import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useLang } from '../../../lib/i18n';
import {
  cancelCreate, createProject, isCancelled, launchProject, listEditors, listTemplates, nameProblem, onCreateProgress,
  pickParentDir, sortTemplates, type HubEditor, type HubProgress, type HubTemplate, type NameProblem,
} from '../../../lib/hub';
import type { RecentWorkspace } from '../../../hooks/home/useRecentWorkspaces';

export type HubView = 'projects' | 'editors' | 'new';
export type HubRun =
  | { state: 'idle' }
  | { state: 'creating'; name: string; progress: HubProgress; cancelling: boolean }
  | { state: 'done'; name: string; path: string; version: string }
  | { state: 'failed'; name: string; error: string }
  | { state: 'cancelled'; name: string };

/** Creating from a template took 85-105 s when measured (29 Oct 2026); the bar is an estimate on that. */
export const EXPECTED_MS = 100_000;
export const estimate = (ms: number) => Math.min(0.95, Math.max(0, ms / EXPECTED_MS));

type ToastKind = 'success' | 'error' | 'warning' | 'info';

export interface HubDeps {
  recent: RecentWorkspace[];
  onSelectWorkspace: (hostPath: string) => Promise<void> | void;
  showToast: (message: string, type: ToastKind) => void;
}

/** All of the mini Hub's state; the per-theme compositions only lay it out. */
export function useHub({ recent, onSelectWorkspace, showToast }: HubDeps) {
  const { t } = useLang();
  const [view, setView] = useState<HubView>('projects');
  // undefined = still looking, null = the call failed, [] = none installed
  const [editors, setEditors] = useState<HubEditor[] | null | undefined>(undefined);
  const [templates, setTemplates] = useState<HubTemplate[] | undefined>(undefined);
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  // Set by a press on Create: missing answers then show why it did not start.
  const [tried, setTried] = useState(false);
  const [parent, setParent] = useState<string | null>(null);
  const [version, setVersionState] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [run, setRun] = useState<HubRun>({ state: 'idle' });
  const versionRef = useRef('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const loadTemplates = useCallback(async (v: string) => {
    setTemplates(undefined);
    const list = v ? sortTemplates(await listTemplates(v)) : [];
    // A later version pick owns the list; an answer for an earlier one is dropped.
    if (!alive.current || versionRef.current !== v) return;
    setTemplates(list);
    setTemplateId(prev => (list.some(x => x.id === prev) ? prev : (list.find(x => x.label === '3d') ?? list[0])?.id ?? ''));
  }, []);

  const refreshEditors = useCallback(async () => {
    setEditors(undefined);
    const list = await listEditors();
    if (!alive.current) return;
    setEditors(list);
    const keep = list?.some(e => e.version === versionRef.current) ? versionRef.current : list?.[0]?.version ?? '';
    versionRef.current = keep;
    setVersionState(keep);
    void loadTemplates(keep);
  }, [loadTemplates]);

  useEffect(() => { void refreshEditors(); }, [refreshEditors]);

  const setVersion = (v: string) => { versionRef.current = v; setVersionState(v); void loadTemplates(v); };

  const problem: NameProblem = nameProblem(name);
  const canCreate = !problem && !!parent && !!version && templates !== undefined && run.state !== 'creating';

  const pickParent = async () => {
    const chosen = await pickParentDir();
    if (chosen && alive.current) setParent(chosen);
  };

  const create = async () => {
    setNameTouched(true);
    setTried(true);
    if (!canCreate || !parent) return;
    const made = name;
    setRun({ state: 'creating', name: made, progress: { phase: 'starting', elapsedMs: 0 }, cancelling: false });
    const off = onCreateProgress(p => {
      if (alive.current) setRun(r => (r.state === 'creating' ? { ...r, progress: p } : r));
    });
    const res = await createProject({ parentDir: parent, name: made, version, ...(templateId ? { templateId } : {}) });
    off();
    if (!alive.current) return;
    if ('path' in res) setRun({ state: 'done', name: made, path: res.path, version });
    else if (isCancelled(res.error)) setRun({ state: 'cancelled', name: made });
    else setRun({ state: 'failed', name: made, error: res.error || t('hub.failedUnknown') });
  };

  const cancel = async () => {
    setRun(r => (r.state === 'creating' ? { ...r, cancelling: true } : r));
    const stopped = await cancelCreate();
    // Not stopped: Unity had already exited, so the result arrives on its own.
    if (!stopped && alive.current) setRun(r => (r.state === 'creating' ? { ...r, cancelling: false } : r));
  };

  /** Back to the form with the same answers (after a failure or a cancel). */
  const back = () => setRun({ state: 'idle' });
  /** A fresh form for another project. */
  const another = () => { setRun({ state: 'idle' }); setName(''); setNameTouched(false); setTried(false); };

  const openCreated = async () => { if (run.state === 'done') await onSelectWorkspace(run.path); };

  const launch = async (path: string, v?: string) => {
    const res = await launchProject(path, v);
    if (res.opened) showToast(t('hub.launched'), 'success');
    else showToast(t('hub.launchFailed', { error: res.error || t('hub.failedUnknown') }), 'error');
  };

  /** How many recent projects were last saved with each editor version. */
  const usage = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of recent) if (r.unityVersion) m.set(r.unityVersion, (m.get(r.unityVersion) ?? 0) + 1);
    return m;
  }, [recent]);

  const template = templates?.find(x => x.id === templateId);

  return {
    view, setView, editors, refreshEditors, templates, template, templateId, setTemplateId,
    name, setName, nameTouched, setNameTouched, tried, problem, parent, pickParent, version, setVersion,
    canCreate, create, cancel, run, back, another, openCreated, launch, usage, recentCount: recent.length,
  };
}

export type Hub = ReturnType<typeof useHub>;
