import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const css = (name: string) => fs.readFileSync(path.join(__dirname, '..', 'renderer/styles/gm', name), 'utf8')

describe('chat space beside the workspace', () => {
  it('measures the space left by the actual sidebar in a shared container', () => {
    expect(css('shell.css')).toMatch(/\.app-body\s*\{[^}]*grid-column: 2 \/ -1[^}]*container: app-body \/ inline-size/)
    const home = fs.readFileSync(path.join(__dirname, '..', 'renderer/pages/home.tsx'), 'utf8')
    expect(home).toContain('className="app-body"')
    expect(css('workspace.css')).toContain('clamp(300px, var(--ws-w), calc(100cqi - 420px))')
  })

  it('overlays only when the remaining area cannot hold 420px of chat and 300px of panel', () => {
    const workspace = css('workspace.css')
    expect(workspace).toContain('@container app-body (width < 720px)')
    expect(workspace).toMatch(/\.app-right\s*\{[^}]*position: absolute[^}]*grid-row: 1[^}]*box-shadow:/)
    expect(workspace).toContain('width: min(max(300px, var(--ws-w)), 100cqi)')
    expect(css('shell.css')).toMatch(/\.app\[data-screen="ayarlar"\][^{]*\.app-body[^}]*display: none/)
  })

  it('gives the title its own row and keeps the composer input usable', () => {
    const thread = css('thread.css')
    expect(thread).toMatch(/\.title-block\s*\{[^}]*flex-wrap: wrap/)
    expect(thread).toMatch(/@container stage \(max-width: 640px\)\s*\{\s*\.tb-title\s*\{\s*flex: 1 0 100%/)
    expect(thread).toMatch(/\.tb-title\s*\{[^}]*min-width: 0/)
    expect(thread).toMatch(/\.tb-title \.tb-v\s*\{[^}]*word-break: normal[^}]*overflow-wrap: break-word/)
    expect(thread).toMatch(/\.composer textarea\s*\{[^}]*min-width: min\(100%, 220px\)/)
    expect(thread).toMatch(/\.composer textarea::placeholder\s*\{[^}]*white-space: nowrap/)
    expect(css('theme-pafta.thread.css')).toContain('grid-column: 1 / -1')
  })
})
