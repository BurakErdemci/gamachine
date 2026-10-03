import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CreateMenuItem } from '../../lib/sceneEditor';

export type MenuEntry =
  | { kind: 'item'; label: string; en?: boolean; icon?: React.ReactNode; shortcut?: string; disabled?: boolean; run?: () => void; sub?: MenuEntry[] }
  | { kind: 'sep' }
  | { kind: 'label'; label: string };
type Item = Extract<MenuEntry, { kind: 'item' }>;

interface Props {
  entries: MenuEntry[]; x: number; y: number; label: string;
  /** `refocus` is false when the menu closed because the user clicked or switched somewhere else. */
  onClose: (refocus: boolean) => void;
}

const MARGIN = 8;
const usable = (entry: MenuEntry | undefined): entry is Item => entry?.kind === 'item' && !entry.disabled;
const next = (list: MenuEntry[], from: number, step: number) => {
  for (let i = from + step; i >= 0 && i < list.length; i += step) if (usable(list[i])) return i;
  return from;
};

type Folder = { label: string; item?: string; children: Folder[] };
/** Unity's create menu as nested entries: categories and "/" in a label become submenus. */
export function createMenuEntries(items: CreateMenuItem[], pick: (item: string) => void): MenuEntry[] {
  const root: Folder = { label: '', children: [] };
  for (const entry of items) {
    const path = [...entry.category.split('/'), ...entry.label.split('/')].map(part => part.trim()).filter(Boolean);
    if (!path.length) continue;
    let folder = root;
    for (const part of path) {
      let child = folder.children.find(c => c.label === part);
      if (!child) { child = { label: part, children: [] }; folder.children.push(child); }
      folder = child;
    }
    folder.item ??= entry.item;
  }
  const toEntry = (folder: Folder): MenuEntry => {
    if (!folder.children.length) return { kind: 'item', label: folder.label, en: true, run: () => pick(folder.item!) };
    const sub = folder.children.map(toEntry);
    if (folder.item) sub.unshift({ kind: 'item', label: folder.label, en: true, run: () => pick(folder.item!) });
    return { kind: 'item', label: folder.label, en: true, sub };
  };
  // Unity's order: Create Empty first, then the categories, then the remaining top-level items (Camera).
  const first = root.children.filter(c => !c.children.length && c.label.startsWith('Create Empty'));
  const folders = root.children.filter(c => c.children.length);
  const rest = root.children.filter(c => !c.children.length && !first.includes(c));
  return [...first, ...folders, ...rest].map(toEntry);
}

const Chevron = () => <svg className="ic ic-sm mi-arrow" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5" /></svg>;

export const SceneContextMenu: React.FC<Props> = ({ entries, x, y, label, onClose }) => {
  // stack[i] = index (in level i) of the item whose submenu is open as level i + 1; hots has one slot per level.
  const [stack, setStack] = useState<number[]>([]);
  const [hots, setHots] = useState<number[]>(() => [next(entries, -1, 1)]);
  const levels = useRef<(HTMLDivElement | null)[]>([]);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const lists: MenuEntry[][] = [entries];
  for (const index of stack) {
    const opener = lists[lists.length - 1][index];
    if (opener?.kind !== 'item' || !opener.sub) break;
    lists.push(opener.sub);
  }
  const state = useRef({ stack, hots, lists });
  state.current = { stack, hots, lists };

  const openSub = (level: number, index: number, withHot: boolean) => {
    const sub = (lists[level][index] as Item).sub!;
    setStack(previous => [...previous.slice(0, level), index]);
    setHots(previous => [...previous.slice(0, level), index, withHot ? next(sub, -1, 1) : -1]);
  };
  const hover = (level: number, index: number) => {
    const entry = lists[level][index];
    if (!usable(entry)) return;
    if (entry.sub) { if (stack[level] !== index) openSub(level, index, false); return; }
    setStack(previous => previous.slice(0, level));
    setHots(previous => [...previous.slice(0, level), index]);
  };
  const activate = (level: number, index: number) => {
    const entry = lists[level][index];
    if (!usable(entry)) return;
    if (entry.sub) { openSub(level, index, true); return; }
    closeRef.current(true);
    entry.run?.();
  };

  useLayoutEffect(() => {
    levels.current[0]?.focus({ preventScroll: true });
  }, []);
  // Keep every level inside the window: no taller than it (the list scrolls inside), the root menu flips up or
  // left at the pointer when it has no room, a submenu flips to the left of its parent.
  const place = useRef(() => {});
  place.current = () => {
    const width = window.innerWidth, height = window.innerHeight;
    const open = state.current.stack;
    levels.current.forEach((element, level) => {
      if (!element) return;
      element.style.maxHeight = `${Math.max(0, height - 2 * MARGIN)}px`;
      const w = element.offsetWidth, h = Math.min(element.offsetHeight, height - 2 * MARGIN);
      let left = x, top = y;
      if (level > 0) {
        const parent = levels.current[level - 1]?.getBoundingClientRect();
        const opener = levels.current[level - 1]?.querySelector(`[data-i="${open[level - 1]}"]`)?.getBoundingClientRect();
        if (parent && opener) {
          left = parent.right - 4; top = opener.top - 6;
          if (left + w > width - MARGIN) left = parent.left + 4 - w;
        }
      } else {
        if (left + w > width - MARGIN) left = x - w >= MARGIN ? x - w : width - w - MARGIN;
        if (top + h > height - MARGIN && y - h >= MARGIN) top = y - h;
      }
      if (top + h > height - MARGIN) top = height - h - MARGIN;
      element.style.left = `${Math.max(MARGIN, left)}px`;
      element.style.top = `${Math.max(MARGIN, top)}px`;
    });
  };
  // Runs on every render: the list can grow in place (Loading -> the create list) without x, y or the stack changing.
  useLayoutEffect(() => { place.current(); });
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => place.current());
    levels.current.forEach(element => { if (element) observer.observe(element); });
    return () => observer.disconnect();
  }, [lists.length]);
  // Keyboard movement keeps the hot item visible inside a scrolled level ("nearest" alignment, done by
  // hand: auto-scroll.test.tsx keeps the browser call inside the auto-scroll hook).
  useLayoutEffect(() => {
    hots.forEach((hot, level) => {
      const box = levels.current[level];
      const item = hot < 0 ? null : box?.querySelector<HTMLElement>(`[data-i="${hot}"]`);
      if (!box || !item) return;
      const top = item.offsetTop, bottom = top + item.offsetHeight;
      if (top < box.scrollTop) box.scrollTop = top;
      else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
    });
  }, [hots]);

  useEffect(() => {
    // Capture phase, so Esc closes only the topmost menu before any other Esc handler sees it.
    const keydown = (event: KeyboardEvent) => {
      const { stack: open, hots: hot, lists: all } = state.current;
      let level = hot.length - 1;
      while (level > 0 && hot[level] < 0) level -= 1;
      const index = hot[level];
      const list = all[level];
      const entry = list?.[index];
      const handled = () => { event.preventDefault(); event.stopPropagation(); };
      switch (event.key) {
        case 'Escape':
          handled();
          if (open.length) { setStack(open.slice(0, -1)); setHots(hot.slice(0, open.length)); }
          else closeRef.current(true);
          return;
        case 'ArrowDown': case 'ArrowUp': {
          handled();
          const target = next(list, index, event.key === 'ArrowDown' ? 1 : -1);
          setStack(open.slice(0, level)); setHots([...hot.slice(0, level), target]);
          return;
        }
        case 'ArrowRight':
          handled();
          if (usable(entry) && entry.sub) {
            setStack([...open.slice(0, level), index]);
            setHots([...hot.slice(0, level), index, next(entry.sub, -1, 1)]);
          }
          return;
        case 'ArrowLeft':
          handled();
          if (open.length) { setStack(open.slice(0, -1)); setHots(hot.slice(0, open.length)); }
          return;
        case 'Home': case 'End': {
          handled();
          const target = event.key === 'Home' ? next(list, -1, 1) : next(list, list.length, -1);
          setStack(open.slice(0, level)); setHots([...hot.slice(0, level), target]);
          return;
        }
        case 'Enter': case ' ':
          handled();
          if (!usable(entry)) return;
          if (entry.sub) { setStack([...open.slice(0, level), index]); setHots([...hot.slice(0, level), index, next(entry.sub, -1, 1)]); return; }
          closeRef.current(true);
          entry.run?.();
          return;
        case 'Tab':
          handled(); closeRef.current(true); return;
      }
    };
    const outside = (event: MouseEvent) => {
      if (levels.current.some(element => element?.contains(event.target as Node))) return;
      closeRef.current(false);
    };
    const away = () => closeRef.current(false);
    document.addEventListener('keydown', keydown, true);
    document.addEventListener('mousedown', outside, true);
    window.addEventListener('blur', away);
    window.addEventListener('resize', away);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      document.removeEventListener('mousedown', outside, true);
      window.removeEventListener('blur', away);
      window.removeEventListener('resize', away);
    };
  }, []);

  levels.current.length = lists.length;
  return createPortal(<>
    {lists.map((list, level) => (
      <div key={level} ref={element => { levels.current[level] = element; }} className={`r13-menu${level ? ' is-sub' : ''}`} role="menu"
        aria-label={level ? (lists[level - 1][stack[level - 1]] as Item).label : label} tabIndex={-1}
        aria-activedescendant={hots[level] >= 0 ? `se-mi-${level}-${hots[level]}` : undefined}
        style={{ left: x, top: y }} onContextMenu={event => event.preventDefault()}>
        {list.map((entry, index) => entry.kind === 'sep' ? <div key={index} className="mi-sep" role="separator" />
          : entry.kind === 'label' ? <div key={index} className="mi-label" role="presentation">{entry.label}</div>
          : (
            <button key={index} type="button" id={`se-mi-${level}-${index}`} data-i={index} role="menuitem" tabIndex={-1}
              className={`mi${hots[level] === index ? ' is-hot' : ''}`} aria-disabled={entry.disabled || undefined}
              aria-haspopup={entry.sub ? 'menu' : undefined} aria-expanded={entry.sub ? stack[level] === index : undefined}
              onMouseMove={() => { if (hots[level] !== index) hover(level, index); }}
              onClick={() => activate(level, index)}>
              {entry.icon}
              <span className="mi-t" lang={entry.en ? 'en' : undefined}>{entry.label}</span>
              {entry.shortcut && <span className="mi-k">{entry.shortcut}</span>}
              {entry.sub && <Chevron />}
            </button>
          ))}
      </div>
    ))}
  </>, document.body);
};
