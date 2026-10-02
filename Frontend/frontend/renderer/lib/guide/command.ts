/**
 * `/rehber [words]` or `/guide [words]` typed in the composer: the guide opens on those words
 * instead of a message being sent. Returns the search text ('' for none), or null when the line is
 * not the guide command (so `/rehberlik ...` is still an ordinary message).
 */
export function parseGuideCommand(line: string): string | null {
  const m = /^\/(?:rehber|guide)(?:\s+([\s\S]*))?$/i.exec(line.trim());
  return m ? (m[1] ?? '').trim() : null;
}
