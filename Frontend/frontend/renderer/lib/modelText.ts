/**
 * Sanitizers for text the MODEL controls but the UI renders verbatim.
 *
 * Lived inside MessageNotices.tsx until 30 Aug 2026. Moved here when the
 * question card turned out to render model-authored strings too: a second copy
 * would have been the "one gate, two paths" failure this repo keeps measuring —
 * the branch without the gate looks protected because a gate exists somewhere.
 */

/**
 * Strip Unicode bidirectional overrides and isolates.
 *
 * React escapes markup, but U+202E and friends are not markup: the browser
 * honours them and draws the rest of the line in reverse, which is how
 * "safe-name<U+202E>exe.txt" reads as a text file on screen. In a question card
 * the stake is higher than in a notice — the label is not just read, it is the
 * value submitted back, so what the user picks can differ from what they saw.
 *
 * Nothing in this product legitimately needs these characters.
 */
export const stripBidi = (s: string): string =>
  s.replace(/[\u061C\u202A-\u202E\u2066-\u2069\u200E\u200F]/g, '');
