// Prepare actions (REHBER-KAYITLARI.md section 4): named UI states a step needs,
// written `"<action>:<arg>"`. The app implements each one once (home.tsx passes the handlers).
// An action this build cannot perform makes its topic unavailable, the same as a missing anchor.

export type PrepareHandlers = {
  /** chat = the chat on screen; new_chat = the empty new chat; profile = the maker profile. */
  screen: (arg: 'chat' | 'new_chat' | 'profile') => void;
  settings: (page: 'general' | 'models' | 'appearance' | 'unity' | 'approval' | 'remote' | 'account') => void;
  'workspace.tab': (tab: 'scene' | 'files' | 'code' | 'preview') => void;
  'workspace.width': (width: 'narrow' | 'half' | 'focus') => void;
  /** Show the workspace over the chat for one step (the core tour's panel step). */
  'workspace.peek': () => void;
  /** Opens the Preview tab; the app keeps no "most recent asset" list, so the tab shows what is open or its empty state. */
  'preview.open': (kind: 'model' | 'image') => void;
  drawer: (tab: 'terminal' | 'console' | 'problems' | 'connections') => void;
  menu: (which: 'model') => void;
};

/** Every action and argument this build supports. `screen:welcome`, `workspace.tab:browser` and
 *  `preview.open:animation` are named by topics but not built yet. */
export const SUPPORTED: Record<string, readonly (string | null)[]> = {
  screen: ['chat', 'new_chat', 'profile'],
  settings: ['general', 'models', 'appearance', 'unity', 'approval', 'remote', 'account'],
  'workspace.tab': ['scene', 'files', 'code', 'preview'],
  'workspace.width': ['narrow', 'half', 'focus'],
  'workspace.peek': [null],
  'preview.open': ['model', 'image'],
  drawer: ['terminal', 'console', 'problems', 'connections'],
  menu: ['model'],
};

export function parseAction(p: string): { key: string; arg: string | null } {
  const i = p.indexOf(':');
  return i < 0 ? { key: p, arg: null } : { key: p.slice(0, i), arg: p.slice(i + 1) };
}

export function isSupported(p: string): boolean {
  const { key, arg } = parseAction(p);
  return (SUPPORTED[key] ?? []).includes(arg);
}

/** Does this list of actions ask for `key`? (What a step keeps open from the step before.) */
export const wants = (prep: readonly string[], key: string): boolean => prep.some(p => parseAction(p).key === key);

/** Runs the actions in order; an unknown one is skipped with a dev log. */
export function runPrepare(prep: readonly string[], h: PrepareHandlers): void {
  for (const p of prep) {
    const { key, arg } = parseAction(p);
    if (!isSupported(p)) { console.info('[guide] unknown prepare action', p); continue; }
    // The table above vouches for the argument, so the cast only narrows what it already checked.
    (h[key as keyof PrepareHandlers] as (a: string | null) => void)(arg);
  }
}
