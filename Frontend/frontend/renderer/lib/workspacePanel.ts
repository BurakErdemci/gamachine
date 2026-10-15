import { useCallback, useEffect, useRef, useState } from 'react';
import { routeForFile } from '../components/model-viewer/extensions';

/** The workspace tabs (mockup `.ws-tab[data-tab]`): scene, files, code, preview, and the live computer view. */
export type WsTab = 'sahne' | 'dosyalar' | 'kod' | 'onizleme' | 'bilgisayar';
/** The three widths (mockup `.app[data-ws]`): narrow beside the chat, half, focus. */
export type WsWidth = 'dar' | 'yarim' | 'odak';

export const WS_TABS: WsTab[] = ['sahne', 'dosyalar', 'kod', 'onizleme', 'bilgisayar'];
export const WS_WIDTHS: WsWidth[] = ['dar', 'yarim', 'odak'];

/** Which tab shows a path: text opens in Kod, everything the preview panels take in Önizleme. */
export const tabForPath = (path: string): WsTab => (routeForFile(path) === 'text' ? 'kod' : 'onizleme');

/**
 * Panel state for home.tsx: open, tab, width, and `reveal`, the one way something asks to be
 * shown in the panel.
 *
 * Every open request goes through `reveal`, not through an effect on the opened path: an effect
 * only fires when the path CHANGES, so asking again for the file that is already open did
 * nothing once the panel was closed (P2 audit). `reveal` opens the panel, picks the tab and, for
 * Kod and Önizleme, widens a narrow panel to half (mockup HOOKS.md round 7: "From dar, Kod /
 * Önizleme open at half width"); a panel the user made wider is left as it is.
 */
export function useWorkspacePanel(initial: { tab?: WsTab; width?: WsWidth } = {}) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<WsTab>(initial.tab ?? 'sahne');
  const [width, setWidth] = useState<WsWidth>(initial.width ?? 'dar');

  const reveal = useCallback((next: WsTab, opts: { widen?: boolean } = {}) => {
    setOpen(true);
    setTab(next);
    if (opts.widen !== false && (next === 'kod' || next === 'onizleme')) {
      setWidth(w => (w === 'dar' ? 'yarim' : w));
    }
  }, []);

  return { open, setOpen, tab, setTab, width, setWidth, reveal };
}

/**
 * Below this window width only one side column (sidebar or panel) sits beside the chat. At 1100
 * the sidebar (240) and the narrow panel (360) leave the chat 500 px; any narrower and the chat's
 * greeting, cards and composer were squeezed under the panel (owner screenshots, 11 Oct 2026).
 */
export const WS_NARROW_QUERY = '(max-width: 1099px)';

/** Whether the window is below WS_NARROW_QUERY. Without matchMedia (jsdom, old shells) it is wide. */
export function useNarrowWindow(query: string = WS_NARROW_QUERY): boolean {
  const read = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && !!window.matchMedia(query)?.matches;
  const [narrow, setNarrow] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia(query);
    if (!mq || typeof mq.addEventListener !== 'function') return;
    const onChange = () => setNarrow(!!mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return narrow;
}

/**
 * In a narrow window the sidebar and the panel take turns beside the chat, both staying columns
 * (an overlay drawer covered the chat card that asks about the change the panel shows):
 * - the window turns narrow (or the app starts narrow) with both open: the panel folds away;
 * - the panel opens (toggle, a file or change arriving, the terminal): the sidebar folds away,
 *   and comes back when the panel closes;
 * - the sidebar opens: the panel closes;
 * - the window turns wide again: whatever was folded comes back.
 * It reacts to the open states rather than wrapping the setters, because the panel is opened
 * from many places (reveal, arrivals, the guide, the terminal).
 */
export function useOneSideColumnWhenNarrow(
  narrow: boolean,
  side: { open: boolean; setOpen: (open: boolean) => void },
  panel: { open: boolean; setOpen: (open: boolean) => void },
): void {
  const { open: sideOpen, setOpen: setSideOpen } = side;
  const { open: panelOpen, setOpen: setPanelOpen } = panel;
  // `narrow: false` first, so an app that starts narrow goes through the "turned narrow" step.
  const prev = useRef({ narrow: false, side: sideOpen, panel: panelOpen });
  const panelFolded = useRef(false);
  const sideFolded = useRef(false);

  useEffect(() => {
    const was = prev.current;
    prev.current = { narrow, side: sideOpen, panel: panelOpen };

    if (narrow !== was.narrow) {
      if (narrow) {
        if (sideOpen && panelOpen) { panelFolded.current = true; setPanelOpen(false); }
      } else {
        if (panelFolded.current && !panelOpen) setPanelOpen(true);
        if (sideFolded.current && !sideOpen) setSideOpen(true);
        panelFolded.current = false;
        sideFolded.current = false;
      }
      return;
    }
    if (!narrow) return;

    if (panelOpen && !was.panel) panelFolded.current = false;
    if (sideOpen && !was.side) sideFolded.current = false;

    if (sideOpen && panelOpen) {
      // The one opened just now stays; when both opened at once the sidebar wins, as on start.
      if (was.side && !was.panel) { sideFolded.current = true; setSideOpen(false); return; }
      panelFolded.current = true;
      setPanelOpen(false);
      return;
    }
    if (!panelOpen && was.panel && sideFolded.current && !sideOpen) {
      sideFolded.current = false;
      setSideOpen(true);
    }
  }, [narrow, sideOpen, panelOpen, setSideOpen, setPanelOpen]);
}
