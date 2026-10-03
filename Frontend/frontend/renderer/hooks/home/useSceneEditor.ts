import { useCallback, useEffect, useRef, useState } from 'react';
import type { Inspection, SceneTree, SceneVersion } from '../../lib/sceneEditor';
import type { UnityMCPStatus } from './useAIConfig';

interface Options {
  api: string; token?: string; editorOn: boolean; unityStatus: UnityMCPStatus;
  hierarchyVisible: boolean; inspectorVisible: boolean;
}
interface Runtime { inspect: (id: number | null) => void; select: (id: number | null) => void }
type Backoff = { failures: number; until: number };

// A failing endpoint waits 1, 2, 4 ... 30 s between attempts instead of retrying on every poll.
const MAX_BACKOFF = 30_000;
const fresh = (): Backoff => ({ failures: 0, until: 0 });
const failed = (backoff: Backoff) => {
  backoff.failures += 1;
  backoff.until = Date.now() + Math.min(1000 * 2 ** (backoff.failures - 1), MAX_BACKOFF);
};
// Older Unity packages send only `scene`, which then stands in for the split counters.
const treeKey = (v: SceneVersion) => `${v.epoch}|${v.hierarchy ?? v.scene}`;
const inspectKey = (v: SceneVersion) => `${treeKey(v)}|${v.props ?? v.scene}`;

export function useSceneEditor({ api, token, editorOn, unityStatus, hierarchyVisible, inspectorVisible }: Options) {
  const [visible, setVisible] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'visible');
  const [tree, setTree] = useState<SceneTree | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [inspectLoading, setInspectLoading] = useState(false);
  const [error, setError] = useState<number | null>(null);
  const [versionError, setVersionError] = useState<number | null>(null);
  const [inspectError, setInspectError] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const selected = useRef<number | null>(null);
  const version = useRef<SceneVersion | null>(null);
  const runtime = useRef<Runtime | null>(null);
  const hasTree = useRef(false);
  const compiling = useRef(false);
  const needsInspection = useRef(false);
  const localSelection = useRef(0);
  // The version key each view was last loaded at; null = loaded before any version was known.
  const treeLoaded = useRef<string | null>(null);
  const inspectLoaded = useRef<string | null>(null);
  const treeBackoff = useRef(fresh());
  const inspectBackoff = useRef(fresh());
  const active = editorOn && unityStatus === 'connected' && (hierarchyVisible || inspectorVisible) && visible && !!token;

  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);

  useEffect(() => {
    version.current = null; hasTree.current = false; selected.current = null;
    compiling.current = false; needsInspection.current = false;
    treeLoaded.current = null; inspectLoaded.current = null;
    treeBackoff.current = fresh(); inspectBackoff.current = fresh();
    setTree(null); setInspection(null); setSelectedId(null); setStale(false);
    setError(null); setVersionError(null); setInspectError(null);
  }, [api, token, editorOn, unityStatus]);

  useEffect(() => {
    if (!active) { setLoading(false); setInspectLoading(false); return; }
    let alive = true;
    let treePending = false;
    let inspectPending = false;
    let selectionPending = false;
    // At most four requests: one per tree, inspector, version and selection endpoint.
    const requests = new Map<string, AbortController>();
    const controller = (key: string) => {
      requests.get(key)?.abort();
      const next = new AbortController(); requests.set(key, next); return next;
    };
    const headers = { 'X-Session-Token': token! };
    const getTree = async (key: string | null) => {
      const request = controller('tree');
      treePending = true;
      setLoading(!hasTree.current);
      try {
        const response = await fetch(`${api}/scene-editor/tree`, { headers, signal: request.signal });
        if (!response.ok) throw response.status;
        const data: SceneTree = await response.json();
        if (!alive || request.signal.aborted || compiling.current) return;
        hasTree.current = true; treeLoaded.current = key; treeBackoff.current = fresh();
        setTree(data); setError(null);
      } catch (failure) {
        if (!alive || request.signal.aborted) return;
        failed(treeBackoff.current);
        setError(typeof failure === 'number' ? failure : 502);
      } finally {
        if (requests.get('tree') === request) treePending = false;
        if (alive && !request.signal.aborted) setLoading(false);
      }
    };
    const getInspection = async (id: number | null, key = version.current && inspectKey(version.current)) => {
      const request = controller('inspect');
      if (id === null) { needsInspection.current = false; inspectPending = false; setInspection(null); setInspectError(null); setInspectLoading(false); return; }
      needsInspection.current = true;
      if (compiling.current) { inspectPending = false; setInspectLoading(false); return; }
      inspectPending = true;
      setInspectLoading(true);
      try {
        const response = await fetch(`${api}/scene-editor/inspect/${id}`, { headers, signal: request.signal });
        if (!response.ok) throw response.status;
        const data: Inspection = await response.json();
        if (alive && !request.signal.aborted && !compiling.current && selected.current === id) {
          needsInspection.current = false; inspectLoaded.current = key; inspectBackoff.current = fresh();
          setInspection(data); setInspectError(null);
        }
      } catch (failure) {
        if (!alive || request.signal.aborted || selected.current !== id) return;
        if (failure === 404) {
          needsInspection.current = false; inspectBackoff.current = fresh();
          selected.current = null; setSelectedId(null); setInspection(null); setInspectError(null);
        } else {
          failed(inspectBackoff.current);
          setInspectError(typeof failure === 'number' ? failure : 502);
        }
      } finally {
        if (requests.get('inspect') === request) inspectPending = false;
        if (alive && !request.signal.aborted) setInspectLoading(false);
      }
    };
    const postSelection = (id: number | null) => {
      const request = controller('select');
      selectionPending = true;
      void fetch(`${api}/scene-editor/select`, {
        method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }), signal: request.signal,
      }).catch(() => {}).finally(() => { if (requests.get('select') === request) selectionPending = false; });
    };
    const owner: Runtime = { inspect: id => { void getInspection(id); }, select: postSelection };
    runtime.current = owner;
    // No load before the first version: a load without a key could be stamped current after Unity changed
    // during it (audit verify-f1). The immediate poll below fetches the tree and the inspection with their keys.
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      const request = controller('version');
      const selectionAtStart = localSelection.current;
      try {
        const response = await fetch(`${api}/scene-editor/version`, { headers, signal: request.signal });
        if (!response.ok) throw response.status;
        const next: SceneVersion = await response.json();
        if (!alive || request.signal.aborted) return;
        setVersionError(null);
        compiling.current = next.compiling;
        setStale(next.compiling);
        if (next.compiling) {
          if (version.current === null) version.current = next;
          return;
        }
        const previous = version.current;
        version.current = next;
        if (previous && previous.epoch !== next.epoch) { treeBackoff.current = fresh(); inspectBackoff.current = fresh(); }
        if (previous && previous.selection !== next.selection && !selectionPending && selectionAtStart === localSelection.current && next.selectedId !== selected.current) {
          selected.current = next.selectedId; setSelectedId(next.selectedId); setInspection(null);
          inspectBackoff.current = fresh();
          void getInspection(next.selectedId);
        }
        // A view counts as current only after its refetch succeeded; a failed one is retried (after backoff).
        // A load whose key is null (a selection made before the first version) is never stamped current,
        // so the next poll refetches it once.
        const now = Date.now();
        const treeAt = treeKey(next), inspectAt = inspectKey(next);
        if (!treePending && (!hasTree.current || treeLoaded.current !== treeAt) && now >= treeBackoff.current.until) void getTree(treeAt);
        if (selected.current !== null && !inspectPending && (needsInspection.current || inspectLoaded.current !== inspectAt)
          && now >= inspectBackoff.current.until) void getInspection(selected.current, inspectAt);
      } catch (failure) {
        if (alive && !request.signal.aborted) setVersionError(typeof failure === 'number' ? failure : 502);
      } finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => { void poll(); }, 1000);
    return () => {
      alive = false; clearInterval(timer);
      for (const request of requests.values()) request.abort();
      if (runtime.current === owner) runtime.current = null;
    };
  }, [active, api, token]);

  const select = useCallback((id: number | null) => {
    localSelection.current += 1; selected.current = id; inspectBackoff.current = fresh();
    setSelectedId(id); setInspection(null); setInspectError(null);
    runtime.current?.select(id); runtime.current?.inspect(id);
  }, []);
  return { tree, inspection, selectedId, select, loading, inspectLoading, error: error ?? versionError, inspectError, stale };
}
