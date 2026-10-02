import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { clearSeen, isUnseen, loadSeen, saveSeen, type Seen } from '../renderer/lib/changesSeen'
import { ScenePane } from '../renderer/components/home/Workspace'
import { ceviriUygula, LangContext, type Lang } from '../renderer/lib/i18n'

beforeEach(() => localStorage.clear())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const workspace = ['C:', 'Games', 'Demo'].join('\\')
const file = [workspace, 'Assets', 'Player.cs'].join('\\')
const normFile = file.replaceAll('\\', '/').toLowerCase()
const storageKey = `gm-changes-seen:${workspace.replaceAll('\\', '/').toLowerCase()}`

describe('changes seen storage', () => {
  it('normalizes workspace separators, case and trailing slashes, and round trips files', () => {
    const seen = saveSeen(`${workspace}\\\\`, { [file]: 'modified' }, 100)
    expect(seen).toEqual({ v: 1, at: 100, files: { [normFile]: 'modified' } })
    expect(JSON.parse(localStorage.getItem(storageKey)!)).toEqual(seen)
    expect(loadSeen(`${workspace.replaceAll('\\', '/').toUpperCase()}///`)).toEqual(seen)
    clearSeen(`${workspace.toLowerCase()}\\`)
    expect(loadSeen(workspace)).toBeNull()
  })

  it('keeps workspaces independent', () => {
    saveSeen(workspace, { [file]: 'modified' }, 100)
    expect(loadSeen(`${workspace}Other`)).toBeNull()
    clearSeen(`${workspace}Other`)
    expect(loadSeen(workspace)?.at).toBe(100)
  })

  it.each([
    'broken json', 'null', '[]', '{}',
    '{"v":2,"at":100,"files":{}}',
    '{"v":1,"at":"100","files":{}}',
    '{"v":1,"at":100,"files":null}',
    '{"v":1,"at":100,"files":[]}',
    '{"v":1,"at":100,"files":{"key":7}}',
  ])('reads malformed or wrong-version data as null: %s', value => {
    localStorage.setItem(storageKey, value)
    expect(loadSeen(workspace)).toBeNull()
  })

  it.each(['getItem', 'setItem', 'removeItem'] as const)('survives a throwing localStorage.%s', method => {
    vi.spyOn(localStorage, method).mockImplementation(() => { throw new Error('denied') })
    expect(loadSeen(workspace)).toBeNull()
    expect(() => saveSeen(workspace, { [file]: 'modified' }, 100)).not.toThrow()
    expect(() => clearSeen(workspace)).not.toThrow()
  })

  it('survives denied access to localStorage itself', () => {
    vi.stubGlobal('localStorage', undefined)
    expect(loadSeen(workspace)).toBeNull()
    expect(saveSeen(workspace, { [file]: 'modified' }, 100).files).toEqual({ [normFile]: 'modified' })
    expect(() => clearSeen(workspace)).not.toThrow()
  })
})

describe('isUnseen', () => {
  const seen: Seen = { v: 1, at: 100, files: { [normFile]: 'modified' } }
  it.each([
    ['no seen record', file, 'modified', 90, null, true],
    ['same status, older mtime', file, 'modified', 90, seen, false],
    ['same status, equal mtime', file, 'modified', 100, seen, false],
    ['same status, newer mtime', file, 'modified', 101, seen, true],
    ['changed status', file, 'deleted', 90, seen, true],
    ['new key', `${file}.meta`, 'untracked', 90, seen, true],
    ['missing mtime', file, 'modified', undefined, seen, false],
    ['normalized lookup', normFile.toUpperCase(), 'modified', 90, seen, false],
  ] as const)('%s', (_name, key, status, mtime, record, expected) => {
    expect(isUnseen(key, status, mtime, record)).toBe(expected)
  })
})

describe.each(['tr', 'en'] as const)('ScenePane seen actions (%s)', lang => {
  const labels = lang === 'tr'
    ? { ack: 'Gördüm', title: 'Bu listeyi gördüm: yalnız bundan sonra değişenler görünsün', empty: 'Yeni değişiklik yok · 3 dosyayı gördün.', more: '3 dosyayı gördün.', all: 'Hepsini göster' }
    : { ack: 'Mark as seen', title: 'Hide these until they change again', empty: 'No new changes · 3 files marked as seen.', more: '3 files marked as seen.', all: 'Show all' }
  const base: React.ComponentProps<typeof ScenePane> = {
    change: null, changed: [], changedTotal: 0, isRepo: true,
    workspacePath: workspace, onShowChange: vi.fn(), onOpen: vi.fn(),
  }
  const changed = [{ path: file, rel: 'Assets/Player.cs', status: 'modified' }]
  const pane = (props: Partial<typeof base> = {}, language: Lang = lang) => render(
    <LangContext.Provider value={{ lang: language, setLang: vi.fn(), t: (k, v) => ceviriUygula(language, k, v) }}>
      <ScenePane {...base} {...props} />
    </LangContext.Provider>,
  )

  it('shows the ack action with changed files and invokes its callback', () => {
    const onAck = vi.fn()
    pane({ changed, changedTotal: 1, onAck })
    const button = screen.getByRole('button', { name: labels.title })
    expect(button.textContent).toBe(labels.ack)
    expect(button.title).toBe(labels.title)
    expect(button.className).toBe('ws-ack')
    expect(button.closest('h3')?.className).toBe('ws-label')
    fireEvent.click(button)
    expect(onAck).toHaveBeenCalledOnce()
  })

  it('does not show ack without a callback', () => {
    pane({ changed, changedTotal: 1 })
    expect(screen.queryByText(labels.ack)).toBeNull()
  })

  it('does not show ack for an empty list', () => {
    pane({ onAck: vi.fn() })
    expect(screen.queryByText(labels.ack)).toBeNull()
    expect(screen.getByText(ceviriUygula(lang, 'ws.changedEmpty'))).toBeTruthy()
  })

  it('shows the seen note and Show all instead of the empty message', () => {
    const onShowAll = vi.fn()
    pane({ seenHidden: 3, onShowAll, onAck: vi.fn() })
    expect(screen.getByText(labels.empty)).toBeTruthy()
    expect(screen.queryByText(ceviriUygula(lang, 'ws.changedEmpty'))).toBeNull()
    expect(screen.queryByText(labels.ack)).toBeNull()
    const button = screen.getByRole('button', { name: labels.all })
    expect(button.className).toBe('ws-ack')
    fireEvent.click(button)
    expect(onShowAll).toHaveBeenCalledOnce()
  })

  it('shows the seen count below a non-empty list and invokes Show all', () => {
    const onShowAll = vi.fn()
    const { container } = pane({ changed, changedTotal: 1, seenHidden: 3, onShowAll })
    expect(screen.getByText(labels.more)).toBeTruthy()
    const note = screen.getByText(labels.more).closest('p')!
    expect(note.className).toBe('ws-note')
    expect(container.querySelector('ul')!.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: labels.all }))
    expect(onShowAll).toHaveBeenCalledOnce()
  })

  it('keeps a pending approval visible and never acknowledges it', () => {
    const onShowChange = vi.fn()
    pane({
      change: { id: 'pending', name: 'Approval.cs', original: '', modified: 'class Approval {}', accept: vi.fn(), reject: vi.fn() },
      onShowChange, seenHidden: 3, onShowAll: vi.fn(), onAck: vi.fn(),
    })
    expect(screen.queryByText(labels.ack)).toBeNull()
    expect(screen.queryByText(labels.empty)).toBeNull()
    expect(screen.getByText(labels.more)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Approval.cs/ }))
    expect(onShowChange).toHaveBeenCalledOnce()
  })

  it('preserves the no-repository message without the new props', () => {
    pane({ isRepo: false })
    expect(screen.getByText(ceviriUygula(lang, 'ws.changedNoRepo'))).toBeTruthy()
    expect(screen.queryByRole('button', { name: labels.all })).toBeNull()
  })
})
