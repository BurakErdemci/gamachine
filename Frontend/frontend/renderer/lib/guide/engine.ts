// The tour engine's geometry, ported from the mockup's round 12 script (`place`), as a pure
// function: given where the target and the card are, it says where the spotlight, the card, the
// mascot, the caret (Sade) and the leader line (Pafta, Atölye) go. GuideTour measures and writes.
import type { Align, Pose, Side } from './types';

export interface Box { left: number; top: number; width: number; height: number }

export interface PlaceInput {
  /** The overlay's own box (the app frame); everything below is relative to it. */
  frame: { width: number; height: number };
  /** The anchor's box relative to the frame, or null for a centred card. */
  target: Box | null;
  /** The card (figure + box) and the box inside it. */
  card: { width: number; height: number };
  box: { width: number; height: number };
  /** The rendered mascot figure's width (0 when the theme shows none). */
  figWidth: number;
  side?: Side;
  align?: Align;
  pose?: Pose;
  /** Theme numbers read from CSS custom properties (base.css round 12 tokens). */
  css: { pad: number; gap: number; figToward: boolean; leadInset: number; leadSag: number };
}

export interface PlaceOutput {
  side: Side | 'center';
  hole: Box;
  card: { left: number; top: number };
  /** Which side of the box the figure stands on, whether it is mirrored, and its pose. */
  fig: 'left' | 'right';
  flip: boolean;
  pose: Exclude<Pose, 'auto'>;
  /** px along the box edge facing the target (Sade caret, Atölye hole). */
  caret: number | null;
  /** The leader line (quadratic curve) from the box edge to the spotlight edge, and its end dot. */
  lead: { d: string; x: number; y: number } | null;
}

const M = 16; // the card keeps this far from the frame's edges

export function place(p: PlaceInput): PlaceOutput {
  const { frame: { width: W, height: H }, card: { width: cw, height: ch }, css } = p;
  if (!p.target) {
    return {
      side: 'center',
      hole: { left: W / 2, top: H / 2, width: 0, height: 0 },
      card: { left: Math.round((W - cw) / 2), top: Math.round((H - ch) / 2) },
      fig: 'left', flip: false,
      pose: p.pose && p.pose !== 'auto' ? p.pose : 'wave',
      caret: null, lead: null,
    };
  }
  const r = p.target;
  const x = Math.max(3, r.left - css.pad), y = Math.max(3, r.top - css.pad);
  const w = Math.min(r.left + r.width + css.pad, W - 3) - x;
  const h = Math.min(r.top + r.height + css.pad, H - 3) - y;
  const side = p.side ?? 'below';
  const gap = css.gap, tx0 = x + w / 2, ty = y + h / 2;
  const horiz = () => (p.align === 'start' ? x : p.align === 'end' ? x + w - cw : tx0 - cw / 2);
  const vert = () => (p.align === 'start' ? y : p.align === 'end' ? y + h - ch : ty - ch / 2);
  let cx: number, cy: number;
  if (side === 'above') { cy = y - gap - ch; cx = horiz(); }
  else if (side === 'below') { cy = y + h + gap; cx = horiz(); }
  else if (side === 'left') { cx = x - gap - cw; cy = vert(); }
  else { cx = x + w + gap; cy = vert(); }
  cx = Math.max(M, Math.min(W - cw - M, cx));
  cy = Math.max(M, Math.min(H - ch - M, cy));

  // The figure stands on the side of the box nearer the target (Arena: it points at it) or on the
  // far side (--tour-fig-toward: 0, Pafta / Atölye: a scale figure beside a note whose line points).
  const toward = css.figToward;
  const targetLeft = tx0 < cx + cw / 2;
  let figLeft = toward ? targetLeft : !targetLeft;
  if (side === 'left') figLeft = !toward;
  if (side === 'right') figLeft = toward;

  // The box inside the card: after the figure when it stands left, bottom-aligned (flex-end).
  const bx = cx + (figLeft ? p.figWidth : 0), by = cy + (ch - p.box.height);
  const bw = p.box.width, bh = p.box.height;
  let flip: boolean, pose: Exclude<Pose, 'auto'>;
  if (p.pose && p.pose !== 'auto') { flip = toward ? figLeft : !figLeft; pose = p.pose; }
  else if (toward) { flip = figLeft; pose = ty < by ? 'up' : ty > by + bh ? 'low' : 'out'; }
  else { flip = !figLeft; pose = 'out'; }

  // The anchor point on the box edge facing the target, and where the line meets the spotlight.
  let ax: number, ay: number, ex: number, ey: number, caret: number;
  if (side === 'above' || side === 'below') {
    ax = Math.max(bx + 26, Math.min(bx + bw - 26, tx0)); ay = side === 'above' ? by + bh : by;
    ex = Math.max(x + 10, Math.min(x + w - 10, ax)); ey = side === 'above' ? y : y + h;
    caret = ax - bx;
  } else {
    ay = Math.max(by + 26, Math.min(by + bh - 26, ty)); ax = side === 'left' ? bx + bw : bx;
    ey = Math.max(y + 10, Math.min(y + h - 10, ay)); ex = side === 'left' ? x : x + w;
    caret = ay - by;
  }
  const ins = css.leadInset;
  if (side === 'above') ay -= ins; else if (side === 'below') ay += ins; else if (side === 'left') ax -= ins; else ax += ins;
  const mx = (ax + ex) / 2, my = (ay + ey) / 2 + css.leadSag;

  return {
    side,
    hole: { left: x, top: y, width: w, height: h },
    card: { left: Math.round(cx), top: Math.round(cy) },
    fig: figLeft ? 'left' : 'right', flip, pose, caret,
    lead: { d: `M${ax} ${ay} Q${mx} ${my} ${ex} ${ey}`, x: ex, y: ey },
  };
}
