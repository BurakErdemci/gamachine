import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLang } from '../../lib/i18n';
import { inspectorWriteKey } from '../../lib/sceneEditor';
import type { ComponentMenuItem } from '../../lib/sceneEditor';
import type { InspectorActions } from '../../hooks/home/useSceneEditor';
import { InspectorWriteError } from './InspectorFieldEditors';
import { SceneCube } from './HierarchyPanel';

type Row = { category: string } | { entry: ComponentMenuItem };
export function AddComponentPicker({ id, anchor, actions, onClose, onAdded }: {
  id: number; anchor: HTMLButtonElement; actions: InspectorActions; onClose: (focus: boolean) => void; onAdded: (componentId: number) => void;
}) {
  const { t } = useLang();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const [hot, setHot] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();
  const alive = useRef(true);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  const key = inspectorWriteKey.add(id);
  const write = actions.inspectorWrites[key];
  const pending = !!write?.pending;
  const menu = actions.componentMenu;
  const items = menu.state === 'ready' && menu.id === id ? menu.items : [];
  const q = query.trim().toLocaleLowerCase();
  const rows: Row[] = q ? items.filter(item => `${item.label} ${item.category}`.toLocaleLowerCase().includes(q)).map(entry => ({ entry }))
    : category !== null ? items.filter(item => item.category === category).map(entry => ({ entry }))
      : [...[...new Set(items.filter(item => item.category).map(item => item.category))].map(category => ({ category })),
        ...items.filter(item => !item.category).map(entry => ({ entry }))];
  const active = Math.min(hot, Math.max(0, rows.length - 1));
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useLayoutEffect(() => { input.current?.focus({ preventScroll: true }); }, []);
  const place = () => {
    const element = box.current;
    if (!element) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.max(0, Math.min(300, window.innerWidth - 16));
    element.style.width = `${width}px`; element.style.maxHeight = `${Math.max(0, window.innerHeight - 16)}px`;
    element.style.left = `${Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8))}px`;
    const height = element.offsetHeight, below = rect.bottom + 4;
    element.style.top = `${Math.max(8, Math.min(below + height > window.innerHeight - 8 ? rect.top - height - 4 : below, window.innerHeight - height - 8))}px`;
  };
  useLayoutEffect(place);
  useLayoutEffect(() => {
    const element = list.current, row = element?.querySelector<HTMLElement>(`[data-row="${active}"]`);
    if (!element || !row) return;
    if (row.offsetTop < element.scrollTop) element.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > element.scrollTop + element.clientHeight) element.scrollTop = row.offsetTop + row.offsetHeight - element.clientHeight;
  }, [active, query, category, items]);
  useEffect(() => {
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) closeRef.current(false);
    };
    const away = () => closeRef.current(false);
    document.addEventListener('mousedown', outside, true); window.addEventListener('blur', away); window.addEventListener('resize', place);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place);
    if (box.current) observer?.observe(box.current);
    return () => { document.removeEventListener('mousedown', outside, true); window.removeEventListener('blur', away); window.removeEventListener('resize', place); observer?.disconnect(); };
  }, [anchor]);
  const added = (result: unknown) => {
    if (alive.current && result && typeof result === 'object' && typeof (result as { componentId?: unknown }).componentId === 'number') onAdded((result as { componentId: number }).componentId);
  };
  const pick = (index: number) => {
    if (pending) return;
    const row = rows[index]; if (!row) return;
    if ('category' in row) { setCategory(row.category); setHot(0); input.current?.focus(); return; }
    if (!row.entry.present) void actions.addComponent(id, row.entry.item).then(added);
  };
  const back = () => { setCategory(null); setHot(0); input.current?.focus(); };
  const writeError = write?.error;
  const error = writeError?.retry ? { ...writeError, retry: () => Promise.resolve(writeError.retry!()).then(added) } : writeError;
  return createPortal(<div ref={box} className="insp-picker" role="dialog" aria-label={t('sceneEditor.addComponent')} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(true); return; }
    if (event.key === 'Tab') { onClose(false); return; }
    if (pending) return;
    switch (event.key) {
      case 'ArrowDown': event.preventDefault(); setHot(Math.min(rows.length - 1, active + 1)); break;
      case 'ArrowUp': event.preventDefault(); setHot(Math.max(0, active - 1)); break;
      case 'Enter': if (event.target === input.current || event.target === list.current) { event.preventDefault(); pick(active); } break;
      case 'ArrowLeft': if (!query && category !== null) { event.preventDefault(); back(); } break;
    }
  }}>
    <div className="pk-top"><label className="mm-search"><input ref={input} type="text" value={query} aria-label={t('sceneEditor.searchComponents')}
      placeholder={t('sceneEditor.searchComponents')} autoComplete="off" spellCheck={false} aria-controls={listId}
      aria-activedescendant={rows.length ? `${listId}-${active}` : undefined} readOnly={pending}
      onChange={event => { setQuery(event.target.value); setCategory(null); setHot(0); }} /></label></div>
    <div className="pk-crumb">{q ? t('sceneEditor.componentResults', { count: rows.length }) : category !== null
      ? <><button type="button" aria-label={t('sceneEditor.componentBack')} disabled={pending} onClick={back}>‹</button><span>{category}</span></>
      : t('sceneEditor.addComponent')}</div>
    {menu.id !== id || menu.state === 'idle' || menu.state === 'loading' ? <p className="pk-note" role="status">{t('sceneEditor.loading')}</p>
      : menu.state === 'failed' ? <div className="pk-note" role="alert">{t('sceneEditor.componentMenuFailed')} <button type="button" onClick={() => actions.loadComponentMenu(id)}>{t('sceneEditor.retry')}</button></div>
        : <div className="pk-list" ref={list} id={listId} role="listbox" aria-label={t('sceneEditor.components')} aria-activedescendant={rows.length ? `${listId}-${active}` : undefined}>
          {rows.map((row, index) => <button type="button" role="option" key={'category' in row ? row.category : row.entry.item} id={`${listId}-${index}`} data-row={index} tabIndex={-1}
            className={`mi${index === active ? ' is-hot' : ''}`} aria-selected={index === active} aria-disabled={pending || ('entry' in row && row.entry.present) || undefined}
            onMouseMove={() => setHot(index)} onClick={() => pick(index)}>
            {'category' in row ? <><span className="mi-t">{row.category}</span><span aria-hidden="true">›</span></>
              : <><SceneCube className="cmp-ic" /><span className="mi-t">{row.entry.label}</span><span className="pk-cat">{row.entry.present ? t('sceneEditor.alreadyAdded') : row.entry.category}</span></>}
          </button>)}
          {!rows.length && <p className="pk-note">{t('sceneEditor.noComponentMatches')}</p>}
        </div>}
    <InspectorWriteError error={error} disabled={pending} revert={() => actions.clearInspectorWrite(key)} />
    {pending && <p className="pk-note" role="status">{t('sceneEditor.addingComponent')}</p>}
  </div>, document.body);
}
