// The one place that says which capability flags this build has (REHBER-KAYITLARI.md section 3).
// A topic is listed only when every flag in its `available_when` is on here.
import type { Flag } from './types';

export type Caps = Partial<Record<Flag, boolean>>;

/**
 * Build flags. The welcome screen's Unity Hub button exists (it opens Unity Hub over IPC, or the
 * Unity download page when no Hub is found); the rest are announced and off. Turning one on is the
 * whole change a shipped feature makes here; its topic then appears in the guide by itself.
 */
export const BUILD_FLAGS: Caps = {
  'feature.unityHubNewProject': true,
  'feature.animationPreview': false,
  'feature.agentBrowser': false,
  'feature.computerUse': false,
  'integration.apps': false,
  'integration.blender': false,
};

/** Build flags plus the ones read from the open project (re-evaluated when it changes). */
export function capabilities(project: { isGitRepo: boolean }): Caps {
  return { ...BUILD_FLAGS, 'project.git': project.isGitRepo };
}

export function flagsOn(when: Flag | Flag[] | undefined, caps: Caps): boolean {
  const list = when == null ? ['always' as Flag] : Array.isArray(when) ? when : [when];
  return list.every(f => f === 'always' || caps[f] === true);
}
