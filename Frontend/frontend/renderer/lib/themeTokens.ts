import { useEffect, useState } from 'react';

/**
 * Theme tokens for the parts of the app that cannot read CSS themselves: Monaco, xterm and
 * three.js take colours as strings or numbers, so they would keep the theme they were built
 * with. These helpers read the tokens from `:root` and say when the theme changed.
 *
 * Why the values are resolved through an element and not read raw: an unregistered custom
 * property computes to its token text with `var()` substituted but `color-mix()` left as
 * written (tokens.css derives the diff tints that way), and none of the three consumers can
 * parse that. Setting the text as a real `color` and reading it back makes the browser do
 * the mixing.
 */

/** `#rrggbb`, or '' when the token is unset or not a colour (jsdom, SSR). */
export type Hex = string;

const clamp255 = (n: number) => Math.max(0, Math.min(255, Math.round(n)));
const hex2 = (n: number) => clamp255(n).toString(16).padStart(2, '0');

/** Shared by computed CSS colours and the Özel surface model. */
export const oklabToHex = ([L, A, B]: number[]): Hex => {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3;
  const channels = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ].map(v => {
    const c = Math.max(0, Math.min(1, v));
    return hex2((c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055) * 255);
  });
  return `#${channels.join('')}`;
};

/**
 * Parse a computed colour into `#rrggbb` (alpha dropped: every consumer here wants an opaque
 * colour). Chromium serialises legacy colours as `rgb()/rgba()` and `color-mix(in srgb …)`
 * results as `color(srgb r g b)` with 0..1 channels.
 */
export const parseComputedColor = (value: string): Hex => {
  const v = value.trim();
  if (/^#[0-9a-f]{6}$/i.test(v)) return v.toLowerCase();
  if (/^#[0-9a-f]{3}$/i.test(v)) return `#${v.slice(1).split('').map(c => c + c).join('')}`.toLowerCase();
  const rgb = v.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
  if (rgb) return `#${hex2(+rgb[1])}${hex2(+rgb[2])}${hex2(+rgb[3])}`;
  const srgb = v.match(/^color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/i);
  if (srgb) return `#${hex2(+srgb[1] * 255)}${hex2(+srgb[2] * 255)}${hex2(+srgb[3] * 255)}`;
  const lab = v.match(/^ok(lab|lch)\(\s*([^\s]+)\s+([^\s]+)\s+([^\s/)]+)(?:\s*\/\s*[^)]+)?\s*\)$/i);
  if (lab) {
    const number = (text: string) => /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text) ? Number(text) : NaN;
    const L = lab[2].endsWith('%') ? number(lab[2].slice(0, -1)) / 100 : number(lab[2]);
    const a = number(lab[3]);
    const b = lab[1].toLowerCase() === 'lch'
      ? lab[4].toLowerCase() === 'none' ? 0 : number(lab[4].replace(/deg$/i, '')) * Math.PI / 180
      : number(lab[4]);
    if (![L, a, b].every(Number.isFinite)) return '';
    return oklabToHex(lab[1].toLowerCase() === 'lch' ? [L, a * Math.cos(b), a * Math.sin(b)] : [L, a, b]);
  }
  return '';
};

let probe: HTMLSpanElement | null = null;

/** Resolve one token on `:root` to `#rrggbb`; '' when it is not set or not a colour. */
export const readColorToken = (name: string): Hex => {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return '';
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return '';
  const direct = parseComputedColor(raw);
  if (direct) return direct;
  // color-mix() / named colours: let the browser compute them on a hidden element.
  if (!probe || !probe.isConnected) {
    probe = document.createElement('span');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;pointer-events:none';
    document.body.appendChild(probe);
  }
  probe.style.color = '';
  probe.style.color = raw;
  return probe.style.color ? parseComputedColor(getComputedStyle(probe).color) : '';
};

/** Several tokens at once, each falling back to its given default when unresolved. */
export const readColorTokens = <K extends string>(defaults: Record<K, Hex>): Record<K, Hex> => {
  const out = {} as Record<K, Hex>;
  for (const name of Object.keys(defaults) as K[]) out[name] = readColorToken(name) || defaults[name];
  return out;
};

/** Plain (non-colour) token text, e.g. a font stack. */
export const readToken = (name: string): string => {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return '';
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
};

/** WCAG relative luminance of `#rrggbb` (0 black .. 1 white). */
export const luminance = (hex: Hex): number => {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return 0;
  const lin = (c: string) => {
    const s = parseInt(c, 16) / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(m[1]) + 0.7152 * lin(m[2]) + 0.0722 * lin(m[3]);
};

/** A light ground needs the light base theme in Monaco (Pafta and Atolye draw paper editors). */
export const isLight = (hex: Hex): boolean => luminance(hex) > 0.4;

/**
 * Call `cb` whenever the appearance changes. The theme lives in `data-theme` on `<html>`
 * (lib/appearance.ts); the reading / code font pick is an inline style on the same element,
 * so `style` is watched too. One observer per subscriber; they are few (an editor, a
 * terminal, a 3D stage).
 */
export const onThemeChange = (cb: () => void): (() => void) => {
  if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') return () => {};
  const observer = new MutationObserver(() => cb());
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
  return () => observer.disconnect();
};

/** A counter that bumps on every theme change, for effects that rebuild colours from tokens. */
export const useThemeVersion = (): number => {
  const [version, setVersion] = useState(0);
  useEffect(() => onThemeChange(() => setVersion(v => v + 1)), []);
  return version;
};
