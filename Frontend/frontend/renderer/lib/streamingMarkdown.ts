/**
 * Holds back markdown that is still arriving, so a streamed answer never shows
 * half-typed syntax.
 *
 * Why: until its closing `)` arrives, `**[Tune.cs](file:///C:/Unity%20Projeler/...`
 * is not a link to the markdown parser, so it renders as literal characters, the
 * full local path included. Measured on 4.0.0 (12 Nov 2026) in two chats, on
 * screen recordings. Only the last line can be unfinished, so only the last line
 * is touched; a finished line comes back unchanged, so a complete answer renders
 * exactly as it would without this step.
 */

const FENCE = /^ {0,3}(```|~~~)/gm;

/** Same length as `s`, with inline code (and an unclosed trailing backtick run) blanked out. */
const maskCode = (s: string): string => {
  let out = s.replace(/(`+)[^`]*?\1/g, m => ' '.repeat(m.length));
  const open = out.indexOf('`');
  if (open >= 0) out = out.slice(0, open) + ' '.repeat(out.length - open);
  return out;
};

/** Index of the `)` closing a link destination that opens at `from - 1`, or -1. */
const closingParen = (s: string, from: number): number => {
  let depth = 1;
  for (let i = from; i < s.length; i++) {
    if (s[i] === '\\') { i++; continue; }
    if (s[i] === '(') depth++;
    else if (s[i] === ')' && --depth === 0) return i;
  }
  return -1;
};

/** Index of the `]` closing a label that opens at `from - 1`, or -1. */
const closingBracket = (s: string, from: number): number => {
  let depth = 1;
  for (let i = from; i < s.length; i++) {
    if (s[i] === '\\') { i++; continue; }
    if (s[i] === '[') depth++;
    else if (s[i] === ']' && --depth === 0) return i;
  }
  return -1;
};

/** The line with an unfinished link (or image) reduced to its label, and the emphasis opening it dropped. */
const holdBackLink = (line: string): string => {
  const masked = maskCode(line);
  for (let i = 0; i < masked.length; i++) {
    if (masked[i] !== '[' || masked[i - 1] === '\\') continue;
    const close = closingBracket(masked, i + 1);
    const partial = close < 0 || close === masked.length - 1
      || (masked[close + 1] === '(' && closingParen(masked, close + 2) < 0);
    if (!partial) {
      if (masked[close + 1] === '(') i = closingParen(masked, close + 2);
      continue;
    }
    const label = line.slice(i + 1, close < 0 ? line.length : close);
    let start = masked[i - 1] === '!' ? i - 1 : i;
    // An emphasis that opens right at the link cannot have closed yet.
    while (start > 0 && '*_~'.includes(line[start - 1])) start--;
    return line.slice(0, start) + label;
  }
  return line;
};

/** Drops the last `**` on the line when it has no partner yet. */
const holdBackBold = (line: string): string => {
  const masked = maskCode(line);
  const at: number[] = [];
  for (let i = masked.indexOf('**'); i >= 0; i = masked.indexOf('**', i + 2)) at.push(i);
  if (at.length % 2 === 0) return line;
  const last = at[at.length - 1];
  return line.slice(0, last) + line.slice(last + 2);
};

export const holdBackPartialMarkdown = (text: string): string => {
  // Inside an open code block everything is shown verbatim anyway.
  if ((text.match(FENCE)?.length ?? 0) % 2 === 1) return text;
  const cut = text.lastIndexOf('\n') + 1;
  const line = text.slice(cut);
  const held = holdBackBold(holdBackLink(line));
  return held === line ? text : text.slice(0, cut) + held;
};
