/**
 * The guide registry's rules (REHBER-KAYITLARI.md): which topics a build lists (flags, anchors,
 * prepare actions), search with Turkish letters, "new" and "watched", how step N's UI state is
 * built from steps 1..N, the slash command. (That each anchor is written on an element:
 * guide-anchors.test.ts.)
 */
import { describe, expect, it } from 'vitest'

import {
  CORE_TOUR, GUIDE, GROUPS, isNew, isSeen, listedTopics, markHits, matches, registryWarnings, searchWords,
  stateAt, stepsOf, unavailableReason, fold, type GuideContext,
} from '../renderer/lib/guide/registry'
import { BUILD_FLAGS, capabilities } from '../renderer/lib/guide/capabilities'
import { isSupported, runPrepare, SUPPORTED, type PrepareHandlers } from '../renderer/lib/guide/prepare'
import { parseGuideCommand } from '../renderer/lib/guide/command'
import { installedFrom, loadSeen, saveSeen, KEYS, tourDone, markTourDone } from '../renderer/lib/guide/storage'
import { T, type GuideTopic } from '../renderer/lib/guide/types'

const CTX: GuideContext = { caps: capabilities({ isGitRepo: true }) }
const ids = (ts: GuideTopic[]) => ts.map(t => t.id)
const topic = (over: Partial<GuideTopic> = {}): GuideTopic => ({
  id: 'x', group: 'basics', since_version: 'baseline', available_when: 'always',
  title: T('Başlık', 'Title'), summary: T('Özet', 'Summary'),
  steps: [{ anchor: 'composer', title: T('a', 'a'), text: T('b', 'b') }], ...over,
})

describe('registry · what this build lists', () => {
  it('ports the mockup registry whole: 44 topics in 7 groups, a 6-step core tour', () => {
    expect(GUIDE).toHaveLength(44)
    expect(GROUPS.map(g => g.id)).toEqual(['basics', 'unity', 'workspace', 'phone', 'integrations', 'lesser', 'you'])
    expect(stepsOf(CORE_TOUR, CTX)).toHaveLength(6)
    expect(new Set(GUIDE.map(t => t.id)).size).toBe(GUIDE.length)
  })

  it('a flag that is off hides the topic, and turning it on is all it takes', () => {
    const t = topic({ available_when: 'feature.computerUse' })
    expect(unavailableReason(t, CTX)).toBe('flag')
    expect(unavailableReason(t, { caps: { ...CTX.caps, 'feature.computerUse': true } })).toBeNull()
    // every announced flag is off in this build
    expect(Object.values(BUILD_FLAGS).every(v => v === false)).toBe(true)
  })

  it('a topic naming an anchor this build does not know is not listed', () => {
    const t = topic({ steps: [{ anchor: 'no-such-anchor', title: T('a', 'a'), text: T('b', 'b') }] })
    expect(unavailableReason(t, CTX)).toBe('anchor')
    expect(listedTopics(CTX, [t, topic({ id: 'y' })]).map(x => x.id)).toEqual(['y'])
  })

  it('a prepare action the app cannot perform makes the topic unavailable too', () => {
    const t = topic({ steps: [{ anchor: 'composer', prepare: ['screen:welcome'], title: T('a', 'a'), text: T('b', 'b') }] })
    expect(unavailableReason(t, CTX)).toBe('prepare')
    expect(isSupported('screen:welcome')).toBe(false)
    expect(isSupported('workspace.tab:browser')).toBe(false)
    expect(isSupported('preview.open:animation')).toBe(false)
    expect(isSupported('settings:remote')).toBe(true)
  })

  it('placeholders and the Unity Hub topic are hidden; branches and side questions are listed', () => {
    const listed = ids(listedTopics(CTX))
    for (const hidden of ['animation-preview', 'agent-browser', 'computer-use', 'app-support', 'blender', 'new-project', 'name', 'guide']) {
      expect(listed).not.toContain(hidden)
    }
    for (const shown of ['ask', 'branches', 'side-chat', 'code-export', 'changed-files', 'preview', 'themes']) expect(listed).toContain(shown)
    expect(listed).toHaveLength(36)
    // the "Diğer araçlar" group has nothing listed, so the guide does not draw it
    expect(listedTopics(CTX).some(t => t.group === 'integrations')).toBe(false)
  })

  it('changed-files needs a git repository', () => {
    const noGit: GuideContext = { caps: capabilities({ isGitRepo: false }) }
    expect(ids(listedTopics(noGit))).not.toContain('changed-files')
  })

  it('the registry passes its own load checks (rule 4: later screen changes name the screen on step 1)', () => {
    const w = registryWarnings(CTX)
    expect(w.filter(x => x.includes('must name its screen'))).toEqual([])
    // the only drops for an unknown anchor are the not-built Unity Hub topic and the placeholders
    // (all also flag-off, so they report the flag first and give no warning at all)
    expect(w).toEqual([])
  })

  it('every existing topic is baseline: a fresh app shows no "new" noise', () => {
    for (const t of GUIDE) expect(['baseline', 'next']).toContain(t.since_version)
  })
})

describe('registry · search', () => {
  const all = listedTopics(CTX)
  const find = (q: string, lang: 'tr' | 'en' = 'tr') => ids(all.filter(t => matches(t, searchWords(q, lang), lang)))

  it('is case- and Turkish-letter-insensitive', () => {
    expect(find('telefon')).toEqual(['phone-pair', 'phone-approve'])
    expect(find('TELEFON')).toEqual(find('telefon'))
    // "sağ tık" typed without Turkish letters still finds the file tree
    expect(find('sag tik')).toContain('file-tree')
    expect(find('SAĞ TIK')).toContain('file-tree')
    // dotted capital İ and dotless ı fold to i
    expect(fold('İzlendi ılık', 'tr')).toBe('izlendi ilik')
    expect(find('onizleme')).toContain('preview')
  })

  it('every word must match; the other language is not searched', () => {
    expect(find('telefon onay')).toEqual(['phone-approve'])
    // ("microphone" in the dictation keywords also contains "phone")
    expect(find('phone', 'en')).toEqual(['phone-pair', 'phone-approve', 'dictation'])
    expect(find('telefon', 'en')).toEqual([])
    expect(find('xyzzy')).toEqual([])
  })

  it('marks hits in the original text, Turkish letters included', () => {
    const parts = markHits('Sağ tık ve sürükle', searchWords('sag', 'tr'), 'tr')
    expect(parts).toEqual([{ text: 'Sağ', hit: true }, { text: ' tık ve sürükle', hit: false }])
    expect(markHits('Plain', [], 'en')).toEqual([{ text: 'Plain', hit: false }])
  })
})

describe('registry · seen and new', () => {
  const t = topic({ id: 'shiny', since_version: '3.3.0' })

  it('watched = seen rev >= topic rev; raising rev shows it unwatched again', () => {
    expect(isSeen(t, {})).toBe(false)
    expect(isSeen(t, { shiny: 1 })).toBe(true)
    expect(isSeen({ ...t, rev: 2 }, { shiny: 1 })).toBe(false)
  })

  it('new = not watched, shipped after this install started, in the current or previous minor', () => {
    expect(isNew(t, {}, '3.2.0', '3.3.0')).toBe(true)
    expect(isNew(t, {}, '3.2.0', '3.4.5')).toBe(true)
    expect(isNew(t, { shiny: 1 }, '3.2.0', '3.3.0')).toBe(false)     // watched
    expect(isNew(t, {}, '3.3.0', '3.3.0')).toBe(false)                // installed on it: not new to this user
    expect(isNew(t, {}, '3.2.0', '3.5.0')).toBe(false)                // old news
    expect(isNew(t, {}, '3.2.0', '4.0.0')).toBe(false)                // another major
    expect(isNew({ ...t, since_version: 'baseline' }, {}, '0.0.1', '3.3.0')).toBe(false)
    expect(isNew({ ...t, since_version: 'next' }, {}, '0.0.1', '3.3.0')).toBe(false)
  })

  it('storage: seen round-trips, the install version is written once, a refused store breaks nothing', () => {
    const mem = new Map<string, string>()
    const s = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v) } }
    saveSeen({ ask: 1 }, s)
    expect(loadSeen(s)).toEqual({ ask: 1 })
    expect(installedFrom('3.2.0', s)).toBe('3.2.0')
    expect(installedFrom('3.4.0', s)).toBe('3.2.0')
    expect(mem.get(KEYS.installedFrom)).toBe('3.2.0')
    expect(tourDone(s)).toBe(false)
    markTourDone(s)
    expect(tourDone(s)).toBe(true)
    mem.set(KEYS.seen, '{broken')
    expect(loadSeen(s)).toEqual({})
    const refusing = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
    expect(loadSeen(refusing)).toEqual({})
    expect(() => saveSeen({ a: 1 }, refusing)).not.toThrow()
    expect(installedFrom('3.2.0', refusing)).toBe('3.2.0')
    expect(tourDone(refusing)).toBe(false)
  })
})

describe('registry · prepare chaining', () => {
  it('step N carries the prepare actions of the same topic\'s steps 1..N', () => {
    const seq = stepsOf(['drawer'], CTX)
    expect(stateAt(seq, 1)).toEqual(['screen:chat', 'workspace.width:half', 'drawer:terminal'])
    expect(stateAt(seq, 2)).toEqual(['screen:chat', 'workspace.width:half', 'drawer:terminal', 'drawer:problems'])
    const model = stepsOf(['model'], CTX)
    expect(stateAt(model, 3)).toEqual(['screen:chat', 'menu:model', 'menu:model'])
  })

  it('in the core tour a step never inherits another topic\'s actions', () => {
    const seq = stepsOf(CORE_TOUR, CTX)
    expect(seq.map(s => s.topic.id)).toEqual([...CORE_TOUR])
    expect(stateAt(seq, 2)).toEqual(['screen:new_chat'])
    expect(stateAt(seq, 3)).toEqual([])
    expect(stateAt(seq, 5)).toEqual(['workspace.peek'])
  })

  it('runPrepare calls the matching handler with its argument, in order, and skips unknown ones', () => {
    const calls: string[] = []
    const h = Object.fromEntries(Object.keys(SUPPORTED).map(k => [k, (a: string | null) => calls.push(`${k}:${a}`)])) as unknown as PrepareHandlers
    runPrepare(['screen:chat', 'screen:welcome', 'settings:remote', 'workspace.peek'], h)
    expect(calls).toEqual(['screen:chat', 'settings:remote', 'workspace.peek:null'])
  })
})

describe('the /rehber command', () => {
  it('opens the guide with the rest of the line as the search', () => {
    expect(parseGuideCommand('/rehber telefon')).toBe('telefon')
    expect(parseGuideCommand('  /guide  sağ tık ')).toBe('sağ tık')
    expect(parseGuideCommand('/REHBER')).toBe('')
    expect(parseGuideCommand('/rehberlik nedir')).toBeNull()
    expect(parseGuideCommand('rehber')).toBeNull()
    expect(parseGuideCommand('/compact')).toBeNull()
  })
})
