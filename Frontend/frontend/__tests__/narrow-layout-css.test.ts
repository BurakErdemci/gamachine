import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const dir = path.join(__dirname, '..', 'renderer/styles/gm')
const css = (name: string) => fs.readFileSync(path.join(dir, name), 'utf8')
const themeFiles = fs.readdirSync(dir).filter(f => /^theme-.*\.thread\.css$/.test(f)).concat('scene-editor.css')

describe('narrow chat column rules', () => {
  it('keeps the composer on one row', () => {
    const thread = css('thread.css')
    expect(thread).toMatch(/\.composer\s*\{[^}]*flex-wrap: nowrap/)
    expect(thread).toMatch(/\.composer textarea\s*\{[^}]*flex: 1;[^}]*min-width: 0/)
    for (const f of themeFiles) expect(css(f), f).not.toMatch(/\.composer\s*\{[^}]*flex-wrap:\s*wrap/)
  })

  it('stacks the empty hero under 720px and breaks the title between words', () => {
    const thread = css('thread.css')
    expect(thread).toMatch(/@container stage \(max-width: 720px\)\s*\{[^@]*\.empty-hero\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\)/)
    expect(thread).toMatch(/@container stage \(max-width: 720px\)\s*\{[^@]*\.empty-figure\s*\{[^}]*display: none/)
    expect(thread).toMatch(/@container stage \(max-width: 720px\)\s*\{[^@]*\.empty-title\s*\{[^}]*overflow-wrap: normal;[^}]*word-break: normal/)
    expect(thread).toMatch(/@container stage \(max-width: 640px\)\s*\{\s*\.empty\s*\{/)
    // the themes size the figure column with a stronger selector, so each repeats the stacking
    for (const t of ['pafta', 'atolye', 'ozel', 'arena']) {
      expect(css(`theme-${t}.thread.css`), t).toMatch(new RegExp(`@container stage \\(max-width: 720px\\)\\s*\\{\\s*\\[data-theme="${t}"\\] \\.empty-hero\\s*\\{[^}]*minmax\\(0, 1fr\\)`))
    }
  })

  it('lets confirm buttons wrap inside the box', () => {
    const thread = css('thread.css')
    expect(thread).toMatch(/\.confirm-actions\s*\{[^}]*flex-wrap: wrap/)
    expect(thread).toMatch(/\.confirm \.btn\s*\{[^}]*max-width: 100%;[^}]*white-space: normal;[^}]*height: auto;[^}]*min-height: 34px;[^}]*text-align: center/)
    for (const f of themeFiles) expect(css(f), f).not.toMatch(/\.confirm \.btn\s*\{[^}]*[^-]height:\s*\d/)
  })
})
