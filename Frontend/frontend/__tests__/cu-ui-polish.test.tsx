/**
 * Computer-use UI polish (15 Oct live test): D1 settings status layout, D2 workspace tab strip,
 * D3 tool-group kind, D4 launch_app card keeps the full bundle id, D5 live indicator title. jsdom cannot measure
 * widths, so the layout checks read the stylesheet text and the class the markup relies on.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { render, screen, cleanup } from '@testing-library/react'

import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { Workspace } from '../renderer/components/home/Workspace'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { computerCardInfo } from '../renderer/components/home/ComputerUseApproval'
import { summarizeTools } from '../renderer/components/home/ToolGroup'
import { toolKind, ToolBlock } from '../renderer/components/home/ToolBlock'
import { aktifDilAyarla, cevir, translations } from '../renderer/lib/i18n'

const STYLES = path.resolve(__dirname, '../renderer/styles/gm')
const css = (name: string) => fs.readFileSync(path.join(STYLES, name), 'utf8')
/** The declaration body of the first rule whose selector list contains `selector` exactly. */
const rules = (source: string, selector: string): string[] => {
  const out: string[] = []
  const text = source.replace(/\/\*[\s\S]*?\*\//g, '')
  const re = /([^{}]+)\{([^{}]*)\}/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m[1].split(',').map(s => s.trim()).includes(selector)) out.push(m[2])
  }
  return out
}

afterEach(() => { cleanup(); aktifDilAyarla(null) })

describe('D1 · settings status text', () => {
  it('the master row carries a flex layout class that gives the text column the width', () => {
    aktifDilAyarla('tr')
    render(<ComputerUsePage saved={vi.fn()} />)
    const row = screen.getByTestId('cu-master-state').closest('.set-row')!
    expect(row.classList.contains('cu-master-row')).toBe(true)
    const text = css('computer-use.css')
    const col = rules(text, '.cu-master-row .set-rt').join(' ')
    expect(col).toMatch(/min-width:\s*0/)
    expect(col).toMatch(/flex:\s*1/)
    expect(rules(text, '.cu-master-row .set-switch').join(' ')).toMatch(/flex:\s*none/)
    expect(rules(text, '.cu-master-row').join(' ')).toMatch(/display:\s*flex/)
    expect(text).not.toMatch(/word-break:\s*break-all/)
    const hint = rules(text, '.cu-master-row .set-hint').join(' ')
    expect(hint).not.toMatch(/anywhere/)
    expect(hint).not.toMatch(/break-all/)
  })
})

describe('D2 · workspace tabs', () => {
  it('renders the full Bilgisayar label and no stylesheet ellipsises .ws-tab', () => {
    aktifDilAyarla('tr')
    const panes = { sahne: <p>a</p>, dosyalar: <p>b</p>, kod: <p>c</p>, onizleme: <p>d</p>, bilgisayar: <p>e</p> }
    const { container } = render(<Workspace open tab="sahne" onTab={vi.fn()} width="dar" onWidth={vi.fn()} onClose={vi.fn()} panes={panes} drawer={null} />)
    const tabs = container.querySelectorAll('.ws-tab')
    expect(tabs).toHaveLength(5)
    expect(tabs[4].textContent).toBe(translations.tr['ws.tabComputer'])
    for (const file of fs.readdirSync(STYLES).filter(f => f.endsWith('.css'))) {
      const text = css(file)
      for (const sel of ['.ws-tab', '.ws-tabs']) {
        for (const body of rules(text, sel)) expect(body, `${file} ${sel}`).not.toMatch(/text-overflow:\s*ellipsis/)
      }
      for (const m of text.matchAll(/([^{}]*\.ws-tab[^{}]*)\{([^{}]*)\}/g)) {
        expect(m[2], `${file} ${m[1].trim()}`).not.toMatch(/text-overflow:\s*ellipsis/)
      }
    }
    const tab = rules(css('workspace.css'), '.ws-tab').join(' ')
    expect(tab).toMatch(/white-space:\s*nowrap/)
    expect(tab).toMatch(/flex:\s*1 1 auto/)
    expect(tab).toMatch(/min-width:\s*0/)
    expect(tab).toMatch(/font-size:\s*clamp\(10\.5px,[^)]*cqi/)
    expect(rules(css('workspace.css'), '.ws-tabs').join(' ')).toMatch(/container-type:\s*inline-size/)
  })
})

describe('D3 · computer tools are their own kind', () => {
  it('summarises two computer tools as bilgisayar adimi, not Unity', () => {
    aktifDilAyarla('tr')
    const parts = summarizeTools([
      { tool: 'mcp__gamachineComputer__computer_window_state' },
      { tool: 'mcp__gamachineComputer__computer_type_text' },
    ])
    expect(parts.map(p => p.key)).toEqual(['tool.sum.computer'])
    const line = cevir(parts[0].key as any, parts[0].values)
    expect(line).toBe('2 bilgisayar adımı')
    expect(line).not.toContain('Unity')
    expect(translations.en['tool.sum.computer']).toBe('{sayi} computer steps')
  })

  it('classifies bare computer_ names too, and keeps Unity tools as unity', () => {
    expect(toolKind('computer_click')).toBe('computer')
    expect(toolKind('mcp__gamachineComputer__computer_click')).toBe('computer')
    expect(toolKind('mcp__unityMCP__manage_scene')).toBe('unity')
    expect(summarizeTools([{ tool: 'mcp__unityMCP__manage_scene' }]).map(p => p.key)).toEqual(['tool.sum.unity'])
  })

  it('puts the computer part right after unity', () => {
    const keys = summarizeTools([
      { tool: 'mcp__gamachineComputer__computer_click' },
      { tool: 'mcp__unityMCP__manage_scene' },
    ]).map(p => p.key)
    expect(keys).toEqual(['tool.sum.unity', 'tool.sum.computer'])
  })
})

describe('D4 · launch_app card with only a bundle id', () => {
  // Any app can claim a bundle id ending in ".TextEdit"; a name cut from the id would let it pose
  // as the real app on a trust card, so the card names it by the full id until the backend
  // resolves the installed app's real name.
  it('names the app by its full bundle id, never by a segment of it', () => {
    aktifDilAyarla('tr')
    const gate = { gateId: 'cu-1', tool: 'computer_launch_app', workspacePath: '', conversationId: 5,
      computer: computerCardInfo('computer_launch_app', { bundle_id: 'com.evil.TextEdit', risky: false, summary: 'Open it' }) }
    render(<McpApprovalCards {...({
      gate, workspaceMismatch: false, openWorkspacePath: '/ws', onResolved: vi.fn(),
      apiBase: 'http://127.0.0.1:8000', sessionToken: 'tok', showToast: vi.fn(), refreshFileTree: vi.fn(),
      pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingDelete: null, setPendingDelete: vi.fn(),
      pendingCommand: null, setPendingCommand: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    } as any)} />)
    const card = screen.getByTestId('computer-approval')
    expect(card.textContent).toContain(cevir('cu.card.nameFirst', { ad: 'com.evil.TextEdit' }))
    expect(screen.getByTestId('cu-card-app').querySelector('dd')!.textContent!.trim()).toBe('com.evil.TextEdit')
  })
})

describe('D5 · live indicator title', () => {
  it('gives the title no max-width and lets its column take the room', () => {
    const text = css('computer-live.css')
    const title = rules(text, '.cul-tag-title').join(' ')
    expect(title).not.toMatch(/max-width/)
    const col = rules(text, '.cul-tag-text').join(' ')
    expect(col).toMatch(/flex:\s*1/)
    expect(col).toMatch(/min-width:\s*0/)
    expect(rules(text, '.cul-stop').join(' ')).toMatch(/flex:\s*none/)
    const indicator = rules(text, '.cul-ind').join(' ')
    expect(indicator).toMatch(/width:\s*calc\(100vw - 12px\)/)
    expect(indicator).toMatch(/height:\s*calc\(100vh - 12px\)/)
    expect(indicator).toMatch(/position:\s*fixed/)
    const tag = rules(text, '.cul-tag').join(' ')
    expect(tag).toMatch(/width:\s*100%/)
    expect(tag).not.toMatch(/width:\s*\d+px/)
    expect(col).toMatch(/container-type:\s*inline-size/)
    expect(title).toMatch(/font-size:\s*clamp\(11px,[^)]*cqi/)
  })
})

describe('F1 · computer step verb', () => {
  it('renders a translated verb, never the raw key', () => {
    aktifDilAyarla('tr')
    render(<ul><ToolBlock tool="mcp__gamachineComputer__computer_click" args={{ x: 1 }} /></ul>)
    const verb = document.querySelector('.tool-verb')!.textContent!
    expect(verb).toBe('bilgisayar')
    expect(verb).not.toContain('tool.verb')
    expect(translations.en['tool.verb.computer' as any]).toBe('computer')
  })
})

describe('F2 · workspace tab strip in tracked uppercase themes', () => {
  it('tightens arena and pafta tabs without ellipsis and keeps 10.5px or more', () => {
    const text = css('workspace.css').replace(/\/\*[\s\S]*?\*\//g, '')
    const m = text.match(/:is\(\[data-theme="arena"\], \[data-theme="pafta"\]\) \.ws-tab \{([^}]*)\}/)
    expect(m).not.toBeNull()
    expect(m![1]).not.toMatch(/text-overflow/)
    expect(parseFloat(/--ws-tab-fs:\s*([\d.]+)px/.exec(m![1])![1])).toBeGreaterThanOrEqual(10.5)
  })
})
