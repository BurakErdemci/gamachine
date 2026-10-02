// The guide registry schema (REHBER-KAYITLARI.md section 2). A feature ships its topic as one
// object of this shape in `topics/<group>.ts`; the tour and guide code never name a feature.

/** Every user-facing string carries both languages; the guide picks one at render time. */
export type LangPair = { tr: string; en: string };
export const T = (tr: string, en: string): LangPair => ({ tr, en });

export type GroupId = 'basics' | 'unity' | 'workspace' | 'phone' | 'integrations' | 'lesser' | 'you';

/** `always`, or a capability flag (capabilities.ts). All flags of a topic must be on. */
export type Flag =
  | 'always'
  | 'project.git'
  | 'feature.unityHubNewProject'
  | 'feature.animationPreview'
  | 'feature.agentBrowser'
  | 'feature.computerUse'
  | 'integration.apps'
  | 'integration.blender';

export type Side = 'above' | 'below' | 'left' | 'right';
export type Align = 'start' | 'center' | 'end';
export type Pose = 'auto' | 'wave' | 'tag' | 'up' | 'out' | 'low';

export interface GuideStep {
  /** A named anchor (anchors.ts); omitted = a centred card with the mascot. */
  anchor?: string;
  side?: Side;
  align?: Align;
  /** UI state the step needs, `"<action>:<arg>"` (prepare.ts). */
  prepare?: string[];
  pose?: Pose;
  /** A built-in widget in the card: the name field, or the approval-mode sample. */
  ui?: 'name' | 'mode';
  title: LangPair;
  text: LangPair;
  /** Used when the user gave a name; "{name}" is replaced (a vocative, never a suffix). */
  text_named?: LangPair;
}

export interface GuideTopic {
  /** Stable, kebab-case; the storage key for "watched". */
  id: string;
  group: GroupId;
  title: LangPair;
  summary: LangPair;
  /** Extra search words, space separated. */
  keywords?: LangPair;
  /** "3.3.0" | "baseline" (existed before the guide: never "new") | "next" (not shipped). */
  since_version: string;
  /** Raise when the content changes enough to show the topic as unwatched again. Default 1. */
  rev?: number;
  available_when: Flag | Flag[];
  /** A step of the core tour that is not a guide topic. */
  core_only?: boolean;
  steps: GuideStep[];
}

export interface GuideGroup { id: GroupId; title: LangPair; sub: LangPair }
