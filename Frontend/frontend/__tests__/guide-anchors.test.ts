/**
 * Every anchor name the guide treats as known (lib/guide/anchors.ts) is really written as
 * data-guide="..." on an element in the renderer, so a topic can never be listed with a spotlight
 * that points at nothing; and every anchor a listed topic names is one of those.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import { KNOWN_ANCHORS } from '../renderer/lib/guide/anchors'
import { capabilities } from '../renderer/lib/guide/capabilities'
import { listedTopics } from '../renderer/lib/guide/registry'

const CTX = { caps: capabilities({ isGitRepo: true }) }

describe('registry · anchors are real', () => {
  const root = resolve(__dirname, '../renderer')
  const files: string[] = []
  const walk = (d: string) => {
    for (const f of readdirSync(d)) {
      const p = join(d, f)
      // The guide's own code names every anchor; only the components count as declarations.
      if (statSync(p).isDirectory()) { if (!p.endsWith(join('lib', 'guide'))) walk(p) }
      else if (/\.tsx?$/.test(f)) files.push(p)
    }
  }
  walk(root)
  const src = files.map(f => readFileSync(f, 'utf8')).join('\n')
  // data-guide="x", data-guide={... 'x' ...}, guide="x" (settings rows) or Workspace's pane map
  const declared = (name: string) => src.includes(`data-guide="${name}"`) || src.includes(` guide="${name}"`)
    || new RegExp(`data-guide=\\{[^}]*'${name}'`).test(src)
    || new RegExp(`PANE_ANCHOR[^\\n]*: '${name}'`).test(src)

  it.each([...KNOWN_ANCHORS])('"%s" is written on an element', name => {
    expect(declared(name)).toBe(true)
  })

  it('the check itself can fail: an unwritten name is not found', () => {
    expect(declared('computer-use-bar')).toBe(false)
    expect(declared('ws-browser')).toBe(false)
  })

  it('every anchor a listed topic names is one the build knows', () => {
    const known = new Set<string>(KNOWN_ANCHORS)
    for (const t of listedTopics(CTX)) for (const s of t.steps) if (s.anchor) expect(known.has(s.anchor)).toBe(true)
  })
})
