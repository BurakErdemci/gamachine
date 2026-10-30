// What the address bar does with typed text: a web address opens, anything else is searched.
// Electron main still checks every resulting URL against the navigation policy.

const SEARCH_URL = 'https://www.google.com/search?q=';
const HOST_LIKE = /^[\p{L}\p{N}-]+(\.[\p{L}\p{N}-]+)+(:\d{1,5})?([/?#].*)?$/u;

/** The URL to open for the text typed in the address bar, or null when there is nothing to open. */
export function addressToUrl(input: string): string | null {
  const text = input.trim();
  if (!text) return null;
  if (/^https?:\/\//i.test(text)) return text;
  // "github.com", "docs.unity3d.com/Manual": a dotted name without spaces, ending in letters.
  if (!/\s/.test(text) && HOST_LIKE.test(text) && /\.\p{L}{2,}(:\d+)?([/?#]|$)/u.test(text)) return `https://${text}`;
  return SEARCH_URL + encodeURIComponent(text);
}

/** True for the search address `addressToUrl` makes of plain words. */
export function isSearchUrl(url: string): boolean {
  return url.startsWith(SEARCH_URL);
}

/** The short form shown while the bar is not being edited: the site name, without "www.". */
export function displayAddress(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return url;
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}
