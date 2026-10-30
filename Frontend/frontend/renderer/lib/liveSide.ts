import { useMemo, useSyncExternalStore } from 'react';
import type { LiveSide } from './workspacePanel';

export interface LiveSideSnapshot {
  /** The side the Canlı tab shows. */
  side: LiveSide;
  /** The side the user picked by hand, until an auto-switch or that side stopping clears it. */
  manual: LiveSide | null;
  running: Record<LiveSide, boolean>;
}

export const otherSide = (side: LiveSide): LiveSide => (side === 'bilgisayar' ? 'tarayici' : 'bilgisayar');

/**
 * The Canlı tab's view state. A run starting reveals its side (`autoReveal`), except that a side
 * the user picked by hand and that is still running is not taken away from them.
 */
export function createLiveSideState() {
  let snap: LiveSideSnapshot = { side: 'bilgisayar', manual: null, running: { bilgisayar: false, tarayici: false } };
  const listeners = new Set<() => void>();
  const set = (next: LiveSideSnapshot) => { snap = next; listeners.forEach(l => l()); };

  return {
    snapshot: () => snap,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    /** A user click on a sub-tab. */
    pick: (side: LiveSide) => set({ ...snap, side, manual: side }),
    setRunning: (side: LiveSide, on: boolean) => {
      if (snap.running[side] === on) return;
      set({ ...snap, running: { ...snap.running, [side]: on }, manual: !on && snap.manual === side ? null : snap.manual });
    },
    /** Whether the view now shows `side`; false when a running, hand-picked other side kept it. */
    autoReveal: (side: LiveSide): boolean => {
      const other = otherSide(side);
      if (snap.manual === other && snap.running[other]) return false;
      if (snap.side !== side || snap.manual !== null) set({ ...snap, side, manual: null });
      return true;
    },
  };
}

/** A side is drawn while its feature is on or it is running right now. */
export const shownSides = (features: { computer: boolean; browser: boolean }, running: Record<LiveSide, boolean>): Record<LiveSide, boolean> => ({
  bilgisayar: features.computer || running.bilgisayar,
  tarayici: features.browser || running.tarayici,
});

/** The side to draw: the chosen one, or the other when the chosen one is not shown. */
export const viewSide = (side: LiveSide, shown: Record<LiveSide, boolean>): LiveSide =>
  !shown[side] && shown[otherSide(side)] ? otherSide(side) : side;

export function useLiveSide() {
  const store = useMemo(createLiveSideState, []);
  const snap = useSyncExternalStore(store.subscribe, store.snapshot);
  return { ...snap, pick: store.pick, setRunning: store.setRunning, autoReveal: store.autoReveal };
}
