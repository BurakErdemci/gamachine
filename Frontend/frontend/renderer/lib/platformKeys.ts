export function isMacPlatform(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
}

const MAC_KEYS: Record<string, string> = {
  'Ctrl Shift `': '⌘⇧`',
  'Ctrl N': '⌘N',
  'Ctrl S': '⌘S',
  'Ctrl O': '⌘O',
};

export function platformKeys(text: string, mac = isMacPlatform()): string {
  if (!mac) return text;
  return text.replace(/\b(?:Ctrl Shift `|Ctrl N|Ctrl S|Ctrl O)(?!\w)/g, token => MAC_KEYS[token]);
}
