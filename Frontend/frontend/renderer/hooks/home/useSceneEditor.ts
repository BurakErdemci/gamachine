import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchCreateMenu, postSceneWrite } from '../../lib/sceneEditor';
import type { CreateMenuItem, Inspection, SceneTree, SceneVersion, SceneWriteCode } from '../../lib/sceneEditor';
import type { UnityMCPStatus } from './useAIConfig';

interface Options {
  api: string; token?: string; editorOn: boolean; unityStatus: UnityMCPStatus;
  hierarchyVisible: boolean; inspectorVisible: boolean;
}
interface Runtime { inspect: (id: number | null) => void; select: (id: number | null) => void; poll: () => void }
export type CreateMenuState = { state: 'idle' | 'loading' | 'failed' } | { state: 'ready'; items: CreateMenuItem[] };
/** `retry` is absent when retrying cannot help or could run the edit twice; `unsure` = it may have happened, the tree was refreshed. */
export interface SceneWriteError { code: SceneWriteCode | 'busy'; retry?: () => void; unsure?: boolean }
// The create menu changes only with the Unity install or a domain reload, so it is cached per api + token + Unity epoch.
const createMenuCache = new Map<string, CreateMenuItem[]>();
// The user has to change something first; `locked` is a hidden or not-editable object.
const FINAL = new Set<string>(['locked', 'prefab_part', 'invalid_name', 'invalid_value', 'invalid_item', 'not_found']);
// Unity may have run these before the reply was lost; running them again would make a second object.
const NOT_IDEMPOTENT = new Set(['create', 'duplicate']);
// A 503 or a failed fetch can also come after the request reached Unity (a socket closed after the read).
const UNSURE = new Set<string>(['unity_timeout', 'unity_error', 'unity_unavailable']);
// Writes run one at a time in order; more than this many waiting means the user is far ahead of Unity.
const MAX_QUEUED = 8;
type Backoff = { failures: number; until: number };
// A held arrow key moves the selection on every key event; Unity hears the first at once and then only
// the latest, once the keys pause this long.
const SYNC_DELAY = 120;
function coalesce<A>(send: (arg: A) => void) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: { arg: A } | null = null;
  const settle = () => {
    timer = null;
    if (!pending) return;
    const { arg } = pending; pending = null;
    send(arg); timer = setTimeout(settle, SYNC_DELAY);
  };
  return {
    call(arg: A) {
      if (timer === null) { send(arg); timer = setTimeout(settle, SYNC_DELAY); return; }
      pending = { arg }; clearTimeout(timer); timer = setTimeout(settle, SYNC_DELAY);
    },
    busy: () => timer !== null,
    waiting: () => pending !== null,
    cancel() { if (timer !== null) clearTimeout(timer); timer = null; pending = null; },
  };
}

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
  const [writeError, setWriteError] = useState<SceneWriteError | null>(null);
  const [createMenu, setCreateMenu] = useState<CreateMenuState>({ state: 'idle' });
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const queued = useRef(0);
  const menuRequest = useRef('');
  const selected = useRef<number | null>(null);
  const version = useRef<SceneVersion | null>(null);
  const runtime = useRef<Runtime | null>(null);
  const hasTree = useRef(false);
  const compiling = useRef(false);
  const needsInspection = useRef(false);
  const localSelection = useRef(0);
  // Bumped by a selection the user (here or in Unity) made, not by a write selecting its own result.
  const selectionMoves = useRef(0);
  // Bumped when queued writes must not run any more (new Unity epoch, disconnect, editor off, unmount).
  const writeGeneration = useRef(0);
  const deleting = useRef(new Set<number>());
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

  useEffect(() => () => { writeGeneration.current += 1; }, []);

  useEffect(() => {
    writeGeneration.current += 1;
    version.current = null; hasTree.current = false; selected.current = null;
    compiling.current = false; needsInspection.current = false;
    treeLoaded.current = null; inspectLoaded.current = null;
    treeBackoff.current = fresh(); inspectBackoff.current = fresh();
    setTree(null); setInspection(null); setSelectedId(null); setStale(false);
    setError(null); setVersionError(null); setInspectError(null); setWriteError(null);
    setCreateMenu({ state: 'idle' });
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
    const selectSync = coalesce(postSelection);
    const inspectSync = coalesce<number | null>(id => { void getInspection(id); });
    const owner: Runtime = { inspect: inspectSync.call, select: selectSync.call, poll: () => { void poll(); } };
    runtime.current = owner;
    // No load before the first version: a load without a key could be stamped current after Unity changed
    // during it (audit verify-f1). The immediate poll below fetches the tree and the inspection with their keys.
    let polling = false;
    // A poll asked for while one runs (after a write) runs right after it instead of overlapping it.
    let again = false;
    const poll = async (): Promise<void> => {
      if (polling) { again = true; return; }
      polling = true; again = false;
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
        if (previous && previous.epoch !== next.epoch) {
          treeBackoff.current = fresh(); inspectBackoff.current = fresh();
          // Ids from the old epoch mean nothing now: writes still waiting are dropped.
          writeGeneration.current += 1; deleting.current.clear();
        }
        if (previous && previous.selection !== next.selection && !selectionPending && !selectSync.busy() && selectionAtStart === localSelection.current && next.selectedId !== selected.current) {
          selectionMoves.current += 1;
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
        if (selected.current !== null && !inspectPending && !inspectSync.waiting() && (needsInspection.current || inspectLoaded.current !== inspectAt)
          && now >= inspectBackoff.current.until) void getInspection(selected.current, inspectAt);
      } catch (failure) {
        if (alive && !request.signal.aborted) setVersionError(typeof failure === 'number' ? failure : 502);
      } finally {
        polling = false;
        if (again && alive) void poll();
      }
    };
    void poll();
    const timer = setInterval(() => { void poll(); }, 1000);
    return () => {
      alive = false; clearInterval(timer); selectSync.cancel(); inspectSync.cancel();
      for (const request of requests.values()) request.abort();
      if (runtime.current === owner) runtime.current = null;
    };
  }, [active, api, token]);

  const show = useCallback((id: number | null) => {
    localSelection.current += 1; selected.current = id; inspectBackoff.current = fresh();
    setSelectedId(id); setInspection(null); setInspectError(null);
    runtime.current?.select(id); runtime.current?.inspect(id);
  }, []);
  const select = useCallback((id: number | null) => { selectionMoves.current += 1; show(id); }, [show]);
  const dropCreateMenu = useCallback(() => {
    for (const key of [...createMenuCache.keys()]) if (key.startsWith(`${api}|${token}|`)) createMenuCache.delete(key);
  }, [api, token]);
  const loadCreateMenu = useCallback(() => {
    if (!token) return;
    const key = `${api}|${token}|${version.current?.epoch ?? ''}`;
    const cached = createMenuCache.get(key);
    if (cached) { setCreateMenu({ state: 'ready', items: cached }); return; }
    if (menuRequest.current === key) return;
    menuRequest.current = key;
    setCreateMenu({ state: 'loading' });
    const settle = (items: CreateMenuItem[] | null) => {
      if (menuRequest.current !== key) return;
      menuRequest.current = '';
      // An empty or malformed list is never kept, so the next open asks again.
      if (!items?.length) { setCreateMenu({ state: 'failed' }); return; }
      dropCreateMenu(); createMenuCache.set(key, items);
      setCreateMenu({ state: 'ready', items });
    };
    fetchCreateMenu(api, token).then(settle, () => settle(null));
  }, [api, token, dropCreateMenu]);
  // Writes run in order, one at a time: one pressed while another is in flight waits for it instead of
  // being dropped. A success polls at once instead of waiting for the next tick.
  // `replaces` is the error a retry answers: its success clears that error and no other.
  const write = useCallback(<T,>(route: string, body: unknown, done: (data: T) => void, replaces?: SceneWriteError): Promise<T | null> => {
    if (!token) return Promise.resolve(null);
    if (queued.current >= MAX_QUEUED) { setWriteError({ code: 'busy' }); return Promise.resolve(null); }
    queued.current += 1;
    const generation = writeGeneration.current;
    const run = async (): Promise<T | null> => {
      try {
        if (generation !== writeGeneration.current) return null;
        const result = await postSceneWrite<T>(api, token, route, body);
        // Unity reloaded or went away meanwhile: the reply's ids belong to the old scene.
        if (generation !== writeGeneration.current) return null;
        if ('code' in result) {
          const unsure = NOT_IDEMPOTENT.has(route) && UNSURE.has(result.code);
          if (result.code === 'invalid_item') { dropCreateMenu(); menuRequest.current = ''; setCreateMenu({ state: 'idle' }); }
          if (unsure) runtime.current?.poll();
          const error: SceneWriteError = { code: result.code, unsure };
          if (!FINAL.has(result.code) && !unsure) error.retry = () => { void write(route, body, done, error); };
          setWriteError(error);
          return null;
        }
        if (replaces) setWriteError(current => current === replaces ? null : current);
        done(result.data);
        runtime.current?.poll();
        return result.data;
      } finally {
        queued.current -= 1;
        if (queued.current < MAX_QUEUED) setWriteError(current => current?.code === 'busy' ? null : current);
      }
    };
    const next = queue.current.then(run, run);
    queue.current = next.catch(() => null);
    return next;
  }, [api, token, dropCreateMenu]);
  // A late result is selected only if the user has not selected something else since the write was issued.
  const selectResult = useCallback((route: 'create' | 'duplicate', body: unknown) => {
    const moves = selectionMoves.current;
    return write<{ id: number; name: string }>(route, body, data => { if (selectionMoves.current === moves) show(data.id); });
  }, [write, show]);
  const create = useCallback((item: string, parentId: number | null) => selectResult('create', { item, parentId }), [selectResult]);
  const rename = useCallback((id: number, name: string) =>
    write<{ id: number; name: string }>('rename', { id, name }, () => {}), [write]);
  const duplicate = useCallback((id: number) => selectResult('duplicate', { id }), [selectResult]);
  // Only a deleted selection is cleared; another object the user selected meanwhile stays selected.
  // A second Delete of an object whose delete is still waiting adds nothing.
  const remove = useCallback((id: number) => {
    if (deleting.current.has(id)) return Promise.resolve(null);
    deleting.current.add(id);
    const set = deleting.current;
    return write<{ id: number }>('delete', { id }, () => { if (selected.current === id) show(null); })
      .finally(() => { set.delete(id); });
  }, [write, show]);
  const clearWriteError = useCallback(() => setWriteError(null), []);
  return { tree, inspection, selectedId, select, loading, inspectLoading, error: error ?? versionError, inspectError, stale,
    createMenu, loadCreateMenu, create, rename, duplicate, remove, writeError, clearWriteError };
}
