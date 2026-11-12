import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * "Stick to bottom" scrolling for an append-only list (chat, console log).
 *
 * Why this is a shared hook and not an inline effect: the same pattern existed
 * three times in this tree and only ONE of them was guarded. `home.tsx:318` and
 * `TerminalPanel.tsx:101` scrolled unconditionally on every new item, so a user
 * reading older output was yanked back to the bottom; `TerminalPanel.tsx:55`
 * had the guard. Fixing one call site would have left the other two — this
 * repo's recorded failure shape is a fix that closes one of several paths.
 */

/**
 * Distance from the bottom (px) still counted as "at the bottom".
 *
 * Not zero: smooth scrolling lands a pixel or two short and sub-pixel layout
 * makes an exact comparison flip to `false` on its own, which would silently
 * disarm following forever. 40 is the value the terminal console already used.
 *
 * INCLUSIVE: exactly this distance still counts as "at the bottom". The
 * comparison was `<`, so the one position the constant names as the boundary
 * was the one position it excluded — a value documented as the tolerance that
 * was not itself tolerated.
 */
export const BOTTOM_TOLERANCE_PX = 40;

export interface AutoScroll {
  /** Sentinel element ref — render a zero-height div at the end of the list. */
  endRef: React.RefObject<HTMLDivElement>;
  /** Wire to the scrollable container's `onScroll`. */
  onScroll: (e: React.UIEvent<HTMLElement>) => void;
  /** `false` while the user is reading further up. Drives the "jump" affordance. */
  isPinned: boolean;
  /** Scrolls only when the user is still at the bottom. Returns whether it scrolled. */
  followIfPinned: () => boolean;
  /** Scrolls regardless and re-arms following — for content the user MUST see. */
  scrollToBottom: (behavior?: ScrollBehavior) => void;
  /** Re-arms following without scrolling (e.g. when switching conversations). */
  repin: () => void;
}

/** The nearest ancestor that actually scrolls vertically, or null (also in jsdom, which has no layout). */
const scrollingAncestor = (el: HTMLElement | null): HTMLElement | null => {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    if (node.scrollHeight > node.clientHeight) return node;
  }
  return null;
};

export const useAutoScroll = (): AutoScroll => {
  const endRef = useRef<HTMLDivElement>(null);
  // A ref as well as state: the follow decision is read inside effects that must
  // NOT re-run when it flips (re-running would scroll on the very render that
  // recorded "user scrolled away"). The state copy exists only for rendering.
  const pinnedRef = useRef(true);
  const [isPinned, setIsPinned] = useState(true);
  // Last scroll position seen on the container, to tell "the user went up" from
  // "our own follow scroll has not caught up with the content yet".
  const lastTopRef = useRef<{ el: HTMLElement; top: number } | null>(null);
  const growthRef = useRef<{ observer: ResizeObserver; target: Element } | null>(null);

  const setPinned = useCallback((value: boolean) => {
    pinnedRef.current = value;
    setIsPinned(prev => (prev === value ? prev : value));
  }, []);

  // Records where the container is before a programmatic scroll, so the first
  // scroll event that scroll produces is compared with a real starting point.
  const scrollEnd = useCallback((behavior: ScrollBehavior) => {
    // The container `onScroll` already reported wins over the layout guess: an
    // inner box that overflows by a pixel must not take its place.
    const seen = lastTopRef.current?.el;
    const container = seen?.isConnected && endRef.current && seen.contains(endRef.current)
      ? seen : scrollingAncestor(endRef.current);
    if (container) lastTopRef.current = { el: container, top: container.scrollTop };
    endRef.current?.scrollIntoView({ behavior });
  }, []);

  /**
   * Follows growth that is not a new message: an image that finishes loading, a
   * tool card that expands. Neither changes `messages`, so the follow effect in
   * the caller never ran for them and the bottom drifted out of view (measured
   * on 4.0.0, 12 Nov 2026: the view stopped following after the first image).
   * Observes the list the sentinel sits in; re-attaches when that list changes
   * (empty chat -> thread). `auto`, not `smooth`: a smooth animation aims at the
   * bottom as it was when it started and lands short while content keeps growing.
   */
  const watchGrowth = useCallback(() => {
    const target = endRef.current?.parentElement;
    if (!target || typeof ResizeObserver === 'undefined') return;
    if (growthRef.current?.target === target) return;
    growthRef.current?.observer.disconnect();
    const observer = new ResizeObserver(() => { if (pinnedRef.current) scrollEnd('auto'); });
    observer.observe(target);
    growthRef.current = { observer, target };
  }, [scrollEnd]);

  useEffect(() => { watchGrowth(); });
  useEffect(() => () => { growthRef.current?.observer.disconnect(); growthRef.current = null; }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    watchGrowth();
    scrollEnd(behavior);
    setPinned(true);
  }, [setPinned, scrollEnd, watchGrowth]);

  const followIfPinned = useCallback(() => {
    watchGrowth();
    if (!pinnedRef.current) return false;
    scrollEnd('smooth');
    return true;
  }, [scrollEnd, watchGrowth]);

  /**
   * Following stops only when the user moves UP. The position alone used to
   * decide, so the follow scroll's own frames switched following off whenever
   * the content grew faster than the animation (an image arriving mid-scroll
   * left the last frame hundreds of px above the new bottom), and the
   * "new messages" notice appeared for a user who never scrolled. Content growth
   * fires no scroll event and a follow scroll only moves down, so "moved up and
   * is away from the bottom" is a user's act. With no earlier position for this
   * container the position alone decides, as before.
   */
  const onScroll = useCallback((e: React.UIEvent<HTMLElement>) => {
    const el = e.currentTarget;
    const top = el.scrollTop;
    const prev = lastTopRef.current?.el === el ? lastTopRef.current.top : null;
    lastTopRef.current = { el, top };
    if (el.scrollHeight - top - el.clientHeight <= BOTTOM_TOLERANCE_PX) setPinned(true);
    else if (prev === null || top < prev) setPinned(false);
  }, [setPinned]);

  const repin = useCallback(() => setPinned(true), [setPinned]);

  return { endRef, onScroll, isPinned, followIfPinned, scrollToBottom, repin };
};
