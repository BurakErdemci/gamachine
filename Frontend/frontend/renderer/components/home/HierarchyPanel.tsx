import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import type { SceneNode, SceneTree } from '../../lib/sceneEditor';
import type { UnityMCPStatus } from '../../hooks/home/useAIConfig';

interface Props {
  unityStatus: UnityMCPStatus; tree: SceneTree | null; loading: boolean; error: number | null; stale: boolean;
  selectedId: number | null; onSelect: (id: number) => void; onConnect: () => void;
}
type TreeRow = { node: SceneNode; depth: number; path: string };
type Row = TreeRow | { scene: SceneTree['scenes'][number]; count: number };
const ROW_HEIGHT = 26;

export const SceneCube = ({ className = 'hr-ic' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5zM3.5 6.25L10 10l6.5-3.75M10 10v7.5" /></svg>
);

export const HierarchyPanel: React.FC<Props> = ({ unityStatus, tree, loading, error, stale, selectedId, onSelect, onConnect }) => {
  const { t } = useLang();
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Record<string, Record<number, boolean>>>({});
  const [cursor, setCursor] = useState<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(390);
  const body = useRef<HTMLDivElement>(null);
  const treeElement = useRef<HTMLUListElement>(null);
  const filter = query.trim().toLowerCase();
  const model = useMemo(() => {
    const nodes = new Map(tree?.nodes.map(node => [node.id, node]));
    const children = new Map<number, SceneNode[]>();
    const roots: SceneNode[] = [];
    for (const node of nodes.values()) {
      if (node.parentId === null || !nodes.has(node.parentId)) roots.push(node);
      else {
        const list = children.get(node.parentId) ?? [];
        list.push(node); children.set(node.parentId, list);
      }
    }
    const matches = new Set<number>();
    if (filter) for (const node of nodes.values()) {
      if (!node.name.toLowerCase().includes(filter)) continue;
      let ancestor: SceneNode | undefined = node;
      while (ancestor && !matches.has(ancestor.id)) {
        matches.add(ancestor.id); ancestor = ancestor.parentId === null ? undefined : nodes.get(ancestor.parentId);
      }
    }
    const rows: Row[] = [];
    const visited = new Set<number>();
    const assignedRoots = new Set(tree?.scenes.flatMap(scene => scene.rootIds));
    for (const scene of tree?.scenes ?? []) {
      if (!scene.isLoaded) continue;
      const sceneRootIds = new Set(scene.rootIds);
      const sceneRoots = roots.filter(node => sceneRootIds.has(node.id) || (!assignedRoots.has(node.id) && (node.scene === scene.path || node.scene === scene.name)));
      const all: TreeRow[] = [];
      const stack = [...sceneRoots].reverse().map(node => ({ node, depth: 0, path: scene.path }));
      while (stack.length) {
        const row = stack.pop()!;
        if (visited.has(row.node.id)) continue;
        visited.add(row.node.id); all.push(row);
        const descendants = children.get(row.node.id) ?? [];
        for (let i = descendants.length - 1; i >= 0; i--) stack.push({ node: descendants[i], depth: row.depth + 1, path: scene.path });
      }
      rows.push({ scene, count: all.length });
      let hiddenBelow = Infinity;
      for (const row of all) {
        if (filter) { if (matches.has(row.node.id)) rows.push(row); continue; }
        if (row.depth > hiddenBelow) continue;
        hiddenBelow = Infinity;
        rows.push(row);
        if (!(expanded[row.path]?.[row.node.id] ?? row.depth === 0)) hiddenBelow = row.depth;
      }
    }
    return { rows, nodes, children };
  }, [tree, filter, expanded]);
  const nodeRows = useMemo(() => model.rows.filter((row): row is TreeRow => 'node' in row), [model.rows]);
  const currentId = cursor ?? selectedId ?? nodeRows[0]?.node.id ?? null;
  const isOpen = (row: TreeRow) => !!filter || (expanded[row.path]?.[row.node.id] ?? row.depth === 0);
  const toggle = (row: TreeRow, open = !isOpen(row)) => setExpanded(previous => ({
    ...previous, [row.path]: { ...previous[row.path], [row.node.id]: open },
  }));
  const move = (id: number) => {
    setCursor(id);
    const index = model.rows.findIndex(row => 'node' in row && row.node.id === id);
    if (body.current && index >= 0) {
      const top = index * ROW_HEIGHT;
      if (top < body.current.scrollTop) body.current.scrollTop = top;
      else if (top + ROW_HEIGHT > body.current.scrollTop + height) body.current.scrollTop = top + ROW_HEIGHT - height;
      setScrollTop(body.current.scrollTop);
    }
  };
  useEffect(() => {
    const element = body.current;
    if (!element) return;
    const resize = () => { if (element.clientHeight) setHeight(element.clientHeight); };
    resize();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(resize); observer.observe(element);
    return () => observer.disconnect();
  }, [unityStatus, tree !== null]);
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    setScrollTop(0); setCursor(null);
  }, [filter]);
  useEffect(() => {
    if (cursor !== null && !nodeRows.some(row => row.node.id === cursor)) setCursor(null);
  }, [nodeRows, cursor]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const index = nodeRows.findIndex(row => row.node.id === currentId);
    const row = nodeRows[index];
    if (!row) return;
    const children = model.children.get(row.node.id) ?? [];
    switch (event.key) {
      case 'ArrowDown': move(nodeRows[Math.min(index + 1, nodeRows.length - 1)].node.id); break;
      case 'ArrowUp': move(nodeRows[Math.max(0, index - 1)].node.id); break;
      case 'Home': move(nodeRows[0].node.id); break;
      case 'End': move(nodeRows[nodeRows.length - 1].node.id); break;
      case 'ArrowRight':
        if (children.length && !isOpen(row)) toggle(row, true);
        else if (children.length && nodeRows.some(r => r.node.id === children[0].id)) move(children[0].id);
        break;
      case 'ArrowLeft':
        if (children.length && isOpen(row) && !filter) toggle(row, false);
        else if (row.node.parentId !== null && nodeRows.some(r => r.node.id === row.node.parentId)) move(row.node.parentId);
        break;
      case 'Enter': onSelect(row.node.id); break;
      default: return;
    }
    event.preventDefault();
  };
  const windowSize = Math.ceil(height / ROW_HEIGHT) + 12;
  const start = Math.max(0, Math.min(Math.floor(scrollTop / ROW_HEIGHT) - 6, model.rows.length - windowSize));
  const end = Math.min(model.rows.length, start + windowSize);
  const breadcrumb: string[] = [];
  const crumbVisited = new Set<number>();
  let crumb = currentId === null ? undefined : model.nodes.get(currentId);
  while (crumb && !crumbVisited.has(crumb.id)) {
    crumbVisited.add(crumb.id); breadcrumb.push(crumb.name);
    crumb = crumb.parentId === null ? undefined : model.nodes.get(crumb.parentId);
  }
  breadcrumb.reverse();
  const highlighted = (name: string) => {
    const index = name.toLowerCase().indexOf(filter);
    return !filter || index < 0 ? name : <>{name.slice(0, index)}<mark className="hr-hit">{name.slice(index, index + filter.length)}</mark>{name.slice(index + filter.length)}</>;
  };
  if (unityStatus !== 'connected') return (
    <section className="hier"><div className="hier-state"><p>{t('sceneEditor.notLinked')}</p>
      <button type="button" className="btn btn-ghost" onClick={onConnect}>{t('sceneEditor.connect')}</button></div></section>
  );
  return (
    <section className="hier" aria-label={t('sceneEditor.hierarchy')}>
      <div className="hier-tools"><label className="hier-find">
        <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="4.5" /><path d="M11.5 11.5l5 5" /></svg>
        <input type="search" aria-label={t('sceneEditor.filter')} placeholder={t('sceneEditor.filter')} value={query} onChange={event => setQuery(event.target.value)} />
        {query && <button type="button" className="hier-clear" aria-label={t('sceneEditor.clearFilter')} onClick={() => setQuery('')}>×</button>}
      </label></div>
      {stale && <p className="hier-note"><span className="hier-stale">{t('sceneEditor.stale')}</span> {t('sceneEditor.compiling')}</p>}
      {error !== null && <p className="hier-note" role="alert">{t(error === 503 ? 'sceneEditor.unavailable' : error === 504 ? 'sceneEditor.timeout' : 'sceneEditor.error')}</p>}
      {!!tree && tree.total > 200 && breadcrumb.length > 0 && <div className="hier-crumb" title={breadcrumb.join(' › ')}>{breadcrumb.join(' › ')}</div>}
      {loading && !tree ? <div className="hier-state" role="status">{t('sceneEditor.loading')}</div> : !tree ? null : (
        <>
          <div className="hier-body custom-scrollbar" ref={body} onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
            <ul className="hier-tree" role="tree" tabIndex={0} aria-label={t('sceneEditor.hierarchy')}
              aria-activedescendant={model.rows.slice(start, end).some(row => 'node' in row && row.node.id === currentId) ? `hier-node-${currentId}` : undefined}
              onKeyDown={onKeyDown} ref={treeElement} style={{ height: model.rows.length * ROW_HEIGHT }}>
              {model.rows.slice(start, end).map((row, offset) => 'scene' in row ? (
                <li key={`scene-${row.scene.path}`} className="hier-scene" role="presentation" style={{ top: (start + offset) * ROW_HEIGHT }}>
                  <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16l5-8 4 5 2-3 3 6z" /><circle cx="13" cy="5" r="2" /></svg>
                  <span className="hier-scene-name" title={row.scene.name}>{row.scene.name}{row.scene.isDirty ? ' *' : ''}</span>
                  <span className="hier-count">{t('sceneEditor.objects', { count: row.count })}</span>
                </li>
              ) : (
                <li key={row.node.id} id={`hier-node-${row.node.id}`} role="treeitem" aria-label={row.node.name}
                  aria-level={row.depth + 1} aria-selected={selectedId === row.node.id} aria-expanded={row.node.childCount ? isOpen(row) : undefined}
                  className={`hr${row.node.prefab !== 'none' ? ' is-pf' : ''}${!row.node.activeInHierarchy ? ' is-off' : ''}${currentId === row.node.id ? ' is-cursor' : ''}`}
                  style={{ top: (start + offset) * ROW_HEIGHT, paddingLeft: 6 + row.depth * 12 }} title={row.node.name}
                  onClick={() => { move(row.node.id); treeElement.current?.focus(); onSelect(row.node.id); }}>
                  {row.node.childCount ? <button type="button" className="hr-tw" aria-label={row.node.name} tabIndex={-1}
                    onClick={event => { event.stopPropagation(); move(row.node.id); toggle(row); treeElement.current?.focus(); }}>
                    <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d={isOpen(row) ? 'M5 7l5 5 5-5' : 'M7 5l5 5-5 5'} /></svg>
                  </button> : <span className="hr-tw" />}
                  <SceneCube /><span className="hr-name">{highlighted(row.node.name)}</span>
                  {filter && row.node.parentId !== null && <span className="hr-path">{model.nodes.get(row.node.parentId)?.name}</span>}
                </li>
              ))}
            </ul>
            {tree.total === 0 && <div className="hier-state">{t('sceneEditor.emptyScene')}</div>}
            {filter && nodeRows.length === 0 && tree.total > 0 && <div className="hier-state">{t('sceneEditor.noMatches')}</div>}
          </div>
          {tree.truncated && <p className="hier-note">{t('sceneEditor.treeTruncated')}</p>}
        </>
      )}
    </section>
  );
};
