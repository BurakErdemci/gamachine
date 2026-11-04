// renderer/styles/gm/phone.css: every rule is scoped to the phone layout (.app[data-phone]), each of the five
// themes has its own block, the phone sizes hold (44 px targets, 16 px textarea, 15 px body, safe areas), and
// no other stylesheet was touched for it.
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const GM = resolve(__dirname, '../renderer/styles/gm')
const css = readFileSync(resolve(GM, 'phone.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
// Commas inside :is(...) do not separate selectors.
const splitSelectors = (list: string) => {
  const out: string[] = []
  let depth = 0
  let start = 0
  for (let i = 0; i < list.length; i++) {
    if (list[i] === '(') depth++
    else if (list[i] === ')') depth--
    else if (list[i] === ',' && depth === 0) { out.push(list.slice(start, i)); start = i + 1 }
  }
  out.push(list.slice(start))
  return out.map(x => x.trim())
}
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({ selectors: splitSelectors(m[1]), body: m[2] }))
const ruleFor = (selector: string) => rules.find(r => r.selectors.includes(selector))

describe('phone.css', () => {
  it('has rules, none nested, and every selector is under data-phone', () => {
    expect(rules.length).toBeGreaterThan(40)
    expect(css).not.toMatch(/@media|@container|@supports/)
    for (const r of rules) for (const s of r.selectors) expect(s, s).toMatch(/\.app\[data-phone\]/)
  })

  it('has a block for each of the five themes', () => {
    for (const theme of ['arena', 'sade', 'pafta', 'atolye', 'ozel']) {
      const own = rules.filter(r => r.selectors.some(s => s.startsWith(`[data-theme="${theme}"] .app[data-phone]`)))
      expect(own.length, theme).toBeGreaterThan(0)
    }
  })

  it('keeps the phone sizes: 16 px textarea, 15 px body, 44 px targets, safe-area insets', () => {
    expect(ruleFor('.app[data-phone] .composer textarea')?.body).toMatch(/font-size:\s*16px/)
    expect(ruleFor('.app[data-phone]')?.body).toMatch(/--fs-body:\s*15px/)
    for (const s of ['.app[data-phone] .phone-icon-btn', '.app[data-phone] .phone-row', '.app[data-phone] .phone-tab',
      '.app[data-phone] .phone-sheet-row', '.app[data-phone] .approval-actions .btn', '.app[data-phone] .phone-awaiting']) {
      const m = /min-height:\s*(\d+)px/.exec(ruleFor(s)?.body ?? '')
      expect(m, s).toBeTruthy()
      expect(Number(m![1]), s).toBeGreaterThanOrEqual(44)
    }
    // The composer's small remove controls (page chip, attachment) keep their look but get a 44 px hit area.
    for (const s of ['.app[data-phone] .composer-chip-x', '.app[data-phone] .composer-att-x']) {
      const body = ruleFor(s)?.body ?? ''
      expect(Number(/(?:^|[;\s])width:\s*(\d+)px/.exec(body)?.[1]), s).toBeGreaterThanOrEqual(44)
      expect(Number(/(?:^|[;\s])height:\s*(\d+)px/.exec(body)?.[1]), s).toBeGreaterThanOrEqual(44)
    }
    expect(ruleFor('.app[data-phone] .composer-att-x')?.body).toMatch(/opacity:\s*1/)
    for (const side of ['top', 'bottom', 'left', 'right']) expect(css).toContain(`env(safe-area-inset-${side}`)
    // The composer rides the keyboard inset that PhoneChatScreen sets.
    expect(ruleFor('.app[data-phone] .phone-composer.composer-wrap')?.body).toContain('var(--phone-kb')
  })

  it('hides the session-import button: the remote may not import, so the phone shows no dead button', () => {
    expect(ruleFor('.app[data-phone] [data-testid="import-session-open"]')?.body).toMatch(/display:\s*none/)
  })

  it('uses theme tokens, no colour of its own', () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/)
  })

  it('is imported after the base and per-theme files, before the Özel trio', () => {
    const app = readFileSync(resolve(__dirname, '../renderer/pages/_app.tsx'), 'utf8')
    const imports = [...app.matchAll(/import '\.\.\/styles\/gm\/([^']+)'/g)].map(m => m[1])
    expect(imports.slice(-4)).toEqual(['phone.css', 'theme-ozel.shell.css', 'theme-ozel.thread.css', 'theme-ozel.workspace.css'])
  })
})

describe('the other stylesheets', () => {
  it('carry no phone rule: the phone layout lives in phone.css only', () => {
    for (const f of readdirSync(GM).filter(n => n.endsWith('.css') && n !== 'phone.css')) {
      expect(readFileSync(resolve(GM, f), 'utf8'), f).not.toContain('data-phone')
    }
  })

  it('have no uncommitted edit in the working tree (git, when it is there)', () => {
    let changed: string
    try {
      changed = execFileSync('git', ['diff', '--name-only', 'HEAD', '--', '.', ':(exclude)gm/phone.css'], { cwd: resolve(__dirname, '../renderer/styles'), encoding: 'utf8' })
    } catch {
      return // no git here: the scope checks above still hold
    }
    expect(changed.split('\n').filter(Boolean)).toEqual([])
  })
})
