// The guide registry (REHBER-KAYITLARI.md): every topic, the groups, the core tour, and the pure
// rules that decide what is listed, what matches a search and what counts as new or watched.
// Nothing here touches the DOM or storage; engine and screen take the answers from here.
import type { Lang } from '../i18n';
import { platformKeys } from '../platformKeys';
import { flagsOn, type Caps } from './capabilities';
import { isKnownAnchor } from './anchors';
import { isSupported, parseAction } from './prepare';
import { T, type GuideGroup, type GuideStep, type GuideTopic, type LangPair } from './types';
import type { Seen } from './storage';
import { BASICS } from './topics/basics';
import { UNITY } from './topics/unity';
import { WORKSPACE } from './topics/workspace';
import { PHONE } from './topics/phone';
import { INTEGRATIONS } from './topics/integrations';
import { LESSER } from './topics/lesser';
import { YOU } from './topics/you';

export const GUIDE: readonly GuideTopic[] = [...BASICS, ...UNITY, ...WORKSPACE, ...PHONE, ...INTEGRATIONS, ...LESSER, ...YOU];

/** The first-launch tour: topic ids, flattened step by step. Keep each member to one step. */
export const CORE_TOUR = ['name', 'ask', 'unity-switch', 'approvals', 'workspace', 'guide'] as const;

/** Display order; "new arrivals" is derived and drawn above these (never a `group` value). */
export const GROUPS: readonly GuideGroup[] = [
  { id: 'basics', title: T('Temel kullanım', 'Basics'), sub: T('Her gün kullandığın parçalar.', 'The parts you use every day.') },
  { id: 'unity', title: T('Unity', 'Unity'), sub: T("Editor'le bağlantı ve proje.", 'The link to the Editor and your project.') },
  { id: 'workspace', title: T('Çalışma alanı', 'Workspace'), sub: T('Sağdaki panel: kod, önizleme, terminal.', 'The right panel: code, preview, terminal.') },
  { id: 'phone', title: T('Telefon', 'Phone'), sub: T('Masadan kalkınca.', 'When you are away from the desk.') },
  { id: 'integrations', title: T('Diğer araçlar', 'Other tools'), sub: T('Unity dışındaki uygulamalarla çalışmak.', 'Working with apps besides Unity.') },
  { id: 'lesser', title: T('Az bilinenler', 'Lesser known'), sub: T('Göze çarpmayan ama işini kolaylaştıran şeyler.', 'Easy to miss, worth knowing.') },
  { id: 'you', title: T('Profil ve görünüm', 'Profile and looks'), sub: T('Emeğin, temaların, yazı tipin.', 'Your work, themes and fonts.') },
];

const BY_ID = new Map(GUIDE.map(t => [t.id, t]));
export const topicById = (id: string): GuideTopic | undefined => BY_ID.get(id);

export const tx = (o: LangPair | undefined, lang: Lang): string => platformKeys(o ? (o[lang] ?? o.tr) : '');

/** What this build can show: its flags, its anchors and its prepare actions. */
export interface GuideContext {
  caps: Caps;
  isAnchor?: (name: string) => boolean;
  isAction?: (action: string) => boolean;
}

/** Why a topic is not shown, or null when it is. */
export function unavailableReason(t: GuideTopic, ctx: GuideContext): 'flag' | 'anchor' | 'prepare' | null {
  if (!flagsOn(t.available_when, ctx.caps)) return 'flag';
  const anchor = ctx.isAnchor ?? isKnownAnchor;
  const action = ctx.isAction ?? isSupported;
  if (t.steps.some(s => s.anchor && !anchor(s.anchor))) return 'anchor';
  if (t.steps.some(s => (s.prepare ?? []).some(p => !action(p)))) return 'prepare';
  return null;
}
export const isAvailable = (t: GuideTopic | undefined, ctx: GuideContext): t is GuideTopic => !!t && unavailableReason(t, ctx) === null;

/** Topics the guide lists: available and not core-only. */
export const listedTopics = (ctx: GuideContext, all: readonly GuideTopic[] = GUIDE): GuideTopic[] =>
  all.filter(t => !t.core_only && isAvailable(t, ctx));

/**
 * Registry rules worth a dev warning: a flag-on topic dropped for an unknown anchor or an
 * unsupported action, and rule 4 (a topic whose later steps change the screen names its screen on
 * step 1, so Back and a deep link land where stepping forward would).
 */
export function registryWarnings(ctx: GuideContext, all: readonly GuideTopic[] = GUIDE): string[] {
  const out: string[] = [];
  const screenish = (s: GuideStep) => (s.prepare ?? []).some(p => /^(screen|settings)$/.test(parseAction(p).key));
  for (const t of all) {
    const why = unavailableReason(t, ctx);
    if (why === 'anchor') out.push(`[guide] not listed, anchor unknown in this build: ${t.id}`);
    if (why === 'prepare') out.push(`[guide] not listed, prepare action unsupported in this build: ${t.id}`);
    if (t.steps.slice(1).some(screenish) && !screenish(t.steps[0])) out.push(`[guide] registry: step 1 of ${t.id} must name its screen`);
  }
  return out;
}

// ---------- seen / new ----------

export const isSeen = (t: GuideTopic, seen: Seen): boolean => (seen[t.id] ?? 0) >= (t.rev ?? 1);

function vparts(v: string): number[] { return String(v).split('.').map(n => parseInt(n, 10) || 0); }
export function vcmp(a: string, b: string): number {
  const x = vparts(a), y = vparts(b);
  for (let i = 0; i < 3; i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  return 0;
}

/**
 * "New": not watched, shipped in a real version (not baseline / next), after this install started
 * (new to this user, not to the world), and in the current or the previous minor release, so old
 * news drops off by itself and a fresh install sees no "new" noise.
 */
export function isNew(t: GuideTopic, seen: Seen, installedFrom: string, appVersion: string): boolean {
  if (isSeen(t, seen) || !/^\d/.test(t.since_version)) return false;
  const s = vparts(t.since_version), c = vparts(appVersion);
  return vcmp(t.since_version, installedFrom) > 0 && s[0] === c[0] && c[1] - s[1] <= 1 && c[1] >= s[1];
}

// ---------- search ----------

/**
 * Case and Turkish-letter folding that keeps the string length, so a hit found in the folded text
 * can be marked in the original: ş->s, ç->c, ğ->g, ö->o, ü->u, ı/İ->i, each one character.
 */
export function fold(s: string, lang: Lang): string {
  let out = '';
  for (const c of String(s)) {
    let l = c.toLocaleLowerCase(lang === 'tr' ? 'tr' : 'en');
    l = l.normalize('NFD').charAt(0);
    out += l === 'ı' ? 'i' : (l || c);
  }
  return out;
}

export const searchWords = (q: string, lang: Lang): string[] => fold(q.trim(), lang).split(/\s+/).filter(Boolean);

export function matches(t: GuideTopic, words: string[], lang: Lang): boolean {
  if (!words.length) return true;
  const hay = fold([tx(t.title, lang), tx(t.summary, lang), tx(t.keywords, lang)].join(' '), lang);
  return words.every(w => hay.includes(w));
}

/** The text cut into plain and hit parts, for <mark>. Overlapping hits keep the first. */
export function markHits(text: string, words: string[], lang: Lang): { text: string; hit: boolean }[] {
  if (!words.length) return [{ text, hit: false }];
  // Folding per code point keeps the length per code point; index on that array, not on UTF-16.
  const chars = Array.from(text);
  const f = Array.from(fold(text, lang));
  const folded = f.join('');
  const hits: [number, number][] = [];
  for (const w of words) {
    const wl = Array.from(w).length;
    for (let i = 0; i <= f.length - wl; i++) if (f.slice(i, i + wl).join('') === w) hits.push([i, i + wl]);
  }
  if (!hits.length || !folded) return [{ text, hit: false }];
  hits.sort((a, b) => a[0] - b[0]);
  const out: { text: string; hit: boolean }[] = [];
  let at = 0;
  for (const [a, b] of hits) {
    if (a < at) continue;
    if (a > at) out.push({ text: chars.slice(at, a).join(''), hit: false });
    out.push({ text: chars.slice(a, b).join(''), hit: true });
    at = b;
  }
  if (at < chars.length) out.push({ text: chars.slice(at).join(''), hit: false });
  return out;
}

// ---------- steps ----------

export interface SeqItem { topic: GuideTopic; step: GuideStep }

/** The steps of these topics in order; unavailable topics are left out. */
export function stepsOf(ids: readonly string[], ctx: GuideContext): SeqItem[] {
  const out: SeqItem[] = [];
  for (const id of ids) {
    const t = topicById(id);
    if (!isAvailable(t, ctx)) continue;
    for (const step of t.steps) out.push({ topic: t, step });
  }
  return out;
}

/**
 * Within one topic the UI state adds up: step `i` (1-based) runs the prepare actions of that
 * topic's steps 1..i, so a deep link, Back and the arrow keys land on the same screen as stepping
 * forward.
 */
export function stateAt(seq: readonly SeqItem[], i: number): string[] {
  const t = seq[i - 1]?.topic;
  const out: string[] = [];
  for (let k = 0; k < i; k++) if (seq[k].topic === t) out.push(...(seq[k].step.prepare ?? []));
  return out;
}
