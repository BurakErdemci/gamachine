import { useCallback, useState } from 'react';
import { routeForFile } from '../components/model-viewer/extensions';

/** The workspace tabs (mockup `.ws-tab[data-tab]`): scene, files, code, preview. */
export type WsTab = 'sahne' | 'dosyalar' | 'kod' | 'onizleme';
/** The three widths (mockup `.app[data-ws]`): narrow beside the chat, half, focus. */
export type WsWidth = 'dar' | 'yarim' | 'odak';

export const WS_TABS: WsTab[] = ['sahne', 'dosyalar', 'kod', 'onizleme'];
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
