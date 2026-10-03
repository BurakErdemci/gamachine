// The anchor names this build knows (REHBER-KAYITLARI.md section 4). Each one is written as
// `data-guide="<name>"` on a real element; `guide-anchors.test.ts` checks that every name below
// is declared that way in the renderer source, so the list cannot claim an anchor nobody wrote.
//
// Not here, so the topics that name them are not listed:
//   welcome-new-project   the Unity Hub button (next port)
//   ws-preview-animation, ws-browser, computer-use-bar, integration-switches,
//   integration-switch-blender   reserved by announced features
export const KNOWN_ANCHORS = [
  // composer and the strip under it
  'composer', 'composer-input', 'composer-attach', 'composer-mic', 'composer-send',
  'strip-memory', 'strip-usage', 'strip-more',
  // empty chat, top bar
  'quest-board', 'unity-switch', 'unity-light', 'mode-chip',
  'model-chip', 'model-menu', 'model-effort',
  // chat thread
  'approval-pending', 'approval-phone-hint', 'file-chip',
  'thread-actions', 'branch-tabs', 'code-block-actions',
  // sidebar
  'chat-list-active', 'chat-running', 'new-chat', 'side-profile', 'phone-status', 'guide-entry',
  // workspace
  'workspace', 'ws-widths', 'ws-tabs', 'ws-code', 'ws-code-tabs', 'ws-preview', 'file-tree',
  'changed-files', 'drawer',
  // profile
  'profile-shelf',
  // settings
  'settings-search', 'settings-dictation-lang', 'settings-auto-title', 'settings-unity-switch',
  'settings-remote', 'settings-remote-pair', 'settings-themes', 'settings-fonts',
] as const;

export type AnchorName = typeof KNOWN_ANCHORS[number];

const KNOWN = new Set<string>(KNOWN_ANCHORS);
export const isKnownAnchor = (name: string): boolean => KNOWN.has(name);

/**
 * The element a step points at: the first one carrying the anchor that is laid out (an element
 * can carry an anchor while hidden, e.g. a settings page behind another screen).
 */
export function findAnchor(name: string, root: ParentNode = document): HTMLElement | null {
  // Names are kebab-case registry ids, never user text, so no escaping is needed (jsdom has no CSS.escape).
  const all = root.querySelectorAll<HTMLElement>(`[data-guide="${name}"]`);
  for (const el of Array.from(all)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}
