import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { useLang } from '../../../lib/i18n';
import {
  cancelRun, clearRun, getRun, launchProject, listEditors, listTemplates, nameProblem, pickParentDir, sortTemplates,
  startCreate, subscribeRun, type HubEditor, type HubRun, type HubTemplate, type NameProblem,
} from '../../../lib/hub';
import type { RecentWorkspace } from '../../../hooks/home/useRecentWorkspaces';

export type HubView = 'projects' | 'editors' | 'new';
export type { HubRun };

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
  // A run already under way (the screen remounted) is shown first.
  const [view, setView] = useState<HubView>(() => (getRun().state === 'idle' ? 'projects' : 'new'));
  // undefined = still looking, null = the call failed, [] = none installed
  const [editors, setEditors] = useState<HubEditor[] | null | undefined>(undefined);
  // undefined = still looking, null = the call failed
  const [templates, setTemplates] = useState<HubTemplate[] | null | undefined>(undefined);
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  // Set by a press on Create: missing answers then show why it did not start.
  const [tried, setTried] = useState(false);
  const [parent, setParent] = useState<string | null>(null);
  const [version, setVersionState] = useState('');
  const [templateId, setTemplateId] = useState('');
  const run = useSyncExternalStore(subscribeRun, getRun);
  const versionRef = useRef('');
  const alive = useRef(true);
  // Set again on every run: StrictMode runs the cleanup once before the real mount.
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const loadTemplates = useCallback(async (v: string) => {
    setTemplates(undefined);
    const found = v ? await listTemplates(v) : [];
    const list = found && sortTemplates(found);
    // A later version pick owns the list; an answer for an earlier one is dropped.
    if (!alive.current || versionRef.current !== v) return;
    setTemplates(list);
    setTemplateId(prev => (list?.some(x => x.id === prev) ? prev : (list?.find(x => x.label === '3d') ?? list?.[0])?.id ?? ''));
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
  const canCreate = !problem && !!parent && !!version && !!templates && run.state !== 'creating';

  const pickParent = async () => {
    const chosen = await pickParentDir();
    if (chosen && alive.current) setParent(chosen);
  };

  const create = async () => {
    setNameTouched(true);
    setTried(true);
    if (!canCreate || !parent) return;
    void startCreate({ parentDir: parent, name, version, ...(templateId ? { templateId } : {}) }, template);
  };

  const cancel = () => cancelRun();

  /** Back to the form with the run's own answers, so Create sends the same request again (also after a remount). */
  const back = () => {
    if (run.state === 'failed' || run.state === 'cancelled') {
      const { name: n, parentDir, version: v, templateId: tid } = run.req;
      setName(n); setNameTouched(false); setTried(false); setParent(parentDir);
      if (tid) setTemplateId(tid);
      if (v !== versionRef.current) setVersion(v);
    }
    clearRun();
  };
  /** A fresh form for another project. */
  const another = () => { clearRun(); setName(''); setNameTouched(false); setTried(false); };

  // An opened run is consumed: a later mount must not offer the same finished project again.
  const openCreated = async () => { if (run.state === 'done') { await onSelectWorkspace(run.path); clearRun(); } };

  const launch = async (path: string, v?: string) => {
    const res = await launchProject(path, v);
    if (res.opened) { showToast(t('hub.launched'), 'success'); if (run.state === 'done' && run.path === path) clearRun(); }
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
    canCreate, create, cancel, retryTemplates: () => void loadTemplates(versionRef.current), run, back, another, openCreated, launch, usage, recentCount: recent.length,
  };
}

export type Hub = ReturnType<typeof useHub>;
