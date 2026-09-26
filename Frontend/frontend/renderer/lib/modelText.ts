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
 * Invisible zero-width characters go too (U+200B zero width space, U+2060 word
 * joiner, U+FEFF zero width no-break space): they draw nothing, so two
 * different strings look identical on screen. Removing them only drops a line
 * break hint. U+200C and U+200D are kept: Persian and Indic words are spelled
 * with the first, emoji sequences are built with the second.
 *
 * Nothing in this product legitimately needs the removed characters. The main
 * process notifier (`main/helpers/notify.ts`) removes the same ones.
 */
export const stripBidi = (s: string): string =>
  s.replace(/[\u061C\u202A-\u202E\u2066-\u2069\u200B\u200E\u200F\u2060\uFEFF]/g, '');
