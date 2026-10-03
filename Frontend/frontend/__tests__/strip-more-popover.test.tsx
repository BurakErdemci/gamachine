import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: 'idle', elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import { ControlPanel } from '../renderer/components/home/ControlPanel'
import { AnimatedChatInput, type ComposerPickers } from '../renderer/components/ui/animated-ai-chat'
import { translations } from '../renderer/lib/i18n'

afterEach(() => cleanup())

function setup(over: Record<string, unknown> = {}) {
  const props = {
    thinkingLevel: 'medium' as const, setThinkingLevel: () => {},
    isAnalyzingProject: false, activeConvId: 7 as number | null,
    analyzeProject: vi.fn(async () => {}), exportMemory: vi.fn(async () => {}), importMemory: vi.fn(async () => {}),
    compactConversation: vi.fn(async () => {}), isCompacting: false,
    onToggleReports: vi.fn(), reportsOpen: false,
    onAttachFile: vi.fn(), onAddVideo: vi.fn(),
    ...over,
  }
  render(<ControlPanel {...props} />)
  const trigger = screen.getByRole('button', { name: 'Ekle ve sohbet' })
  fireEvent.click(trigger)
  const pop = document.getElementById('strip-more-pop')!
  expect(pop.hidden).toBe(false)
  return { props, pop, trigger }
}

// Rows are `menuitem`, the usage report a `menuitemcheckbox`; DOM order is the keyboard order.
const item = (el: HTMLElement, name: string) =>
  within(el).queryByRole('menuitem', { name }) ?? within(el).getByRole('menuitemcheckbox', { name })
const allItems = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLElement>('[role^="menuitem"]'))

describe('Add & chat menu', () => {
  it('keeps the guide anchor and drops the old "Diğer ayarlar" label', () => {
    const { trigger } = setup()
    expect(trigger.getAttribute('data-guide')).toBe('strip-more')
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu')
    expect(screen.queryByText('Diğer ayarlar')).toBeNull()
    expect(translations.en['strip.more']).toBe('Add & chat')
  })

  it('holds no approval mode list (the top bar chip and Settings set the mode)', () => {
    const { pop } = setup()
    expect(pop.querySelector('[data-mode]')).toBeNull()
    for (const text of ['Modes', 'Otomatik', 'Güvenli Otomatik', 'Adım Adım']) expect(within(pop).queryByText(text)).toBeNull()
    expect(pop.textContent).not.toMatch(/modes/i)
  })

  it('draws two labelled sections, every row with a title and a one-line description', () => {
    const { pop } = setup()
    expect(pop.getAttribute('role')).toBe('menu')
    const groups = within(pop).getAllByRole('group')
    expect(groups.map(g => g.getAttribute('aria-labelledby') && document.getElementById(g.getAttribute('aria-labelledby')!)!.textContent))
      .toEqual(['Sohbete ekle', 'Bu sohbet'])
    const rows: Array<[string, string, number]> = [
      ['Resim veya dosya', 'Bir görsel ya da dosyayı mesaja ekle.', 0],
      ['Video', 'Yerel bir video dosyası ekle; bağlantı için mesaja yapıştır.', 0],
      ['Projeyi öğren', 'Projeyi tarayıp sohbetin hafızasına yazar.', 1],
      ['Hafızayı özetle', 'Sohbeti özetleyip hafızayı boşaltır.', 1],
      ['Kullanım raporu', 'Bu sohbetin model ve maliyet özeti.', 1],
    ]
    for (const [title, desc, g] of rows) {
      const row = item(groups[g], title)
      expect(document.getElementById(row.getAttribute('aria-describedby')!)!.textContent).toBe(desc)
      expect(row.querySelector('svg')).not.toBeNull()
      expect(row.getAttribute('aria-disabled')).toBeNull()
    }
  })

  it('each row calls its own handler and closes the menu', async () => {
    const cases: Array<[string, string]> = [
      ['Resim veya dosya', 'onAttachFile'], ['Video', 'onAddVideo'], ['Projeyi öğren', 'analyzeProject'],
      ['Hafızayı özetle', 'compactConversation'], ['Kullanım raporu', 'onToggleReports'],
    ]
    for (const [name, handler] of cases) {
      const { props, pop } = setup()
      fireEvent.click(item(pop, name))
      expect((props as any)[handler]).toHaveBeenCalledTimes(1)
      for (const [, other] of cases) if (other !== handler) expect((props as any)[other]).not.toHaveBeenCalled()
      expect(pop.hidden).toBe(true)
      cleanup()
    }
  })

  it('the memory submenu still works', async () => {
    const { props, pop } = setup()
    fireEvent.click(item(pop, 'Hafıza seçenekleri'))
    fireEvent.click(item(pop, 'Hafızayı Dışarı Aktar'))
    await waitFor(() => expect(props.exportMemory).toHaveBeenCalledTimes(1))
  })

  it('the usage report row shows whether the panel is open', () => {
    const { pop } = setup({ reportsOpen: true })
    expect(within(pop).getByTestId('reports-toggle').getAttribute('aria-checked')).toBe('true')
  })

  it('rows that do not apply are disabled, say why, and do nothing', () => {
    const { props, pop } = setup({ activeConvId: null })
    for (const name of ['Hafızayı özetle', 'Kullanım raporu']) {
      const row = item(pop, name)
      expect(row.getAttribute('aria-disabled')).toBe('true')
      expect(row.getAttribute('title')).toBe('Önce bir sohbet aç.')
      fireEvent.click(row)
    }
    expect(props.compactConversation).not.toHaveBeenCalled()
    expect(props.onToggleReports).not.toHaveBeenCalled()
    expect(pop.hidden).toBe(false)
    // Learn project opens a chat itself, so it needs none.
    expect(item(pop, 'Projeyi öğren').getAttribute('aria-disabled')).toBeNull()
  })

  it('a running summary and a running analysis disable their rows', () => {
    const { props, pop } = setup({ isCompacting: true, isAnalyzingProject: true })
    const compact = item(pop, 'Özetleniyor...')
    expect(compact.getAttribute('aria-disabled')).toBe('true')
    expect(compact.getAttribute('title')).toBe('Özetleme sürüyor.')
    const learn = item(pop, 'Öğreniyorum...')
    expect(learn.getAttribute('aria-disabled')).toBe('true')
    expect(learn.getAttribute('title')).toBe('Proje analizi sürüyor.')
    fireEvent.click(compact)
    fireEvent.click(learn)
    expect(props.compactConversation).not.toHaveBeenCalled()
    expect(props.analyzeProject).not.toHaveBeenCalled()
  })

  it('keyboard: focus enters on open, arrows move and wrap, Enter activates, Esc returns to the trigger', () => {
    const { props, pop, trigger } = setup()
    const items = allItems(pop)
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(pop, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(pop, { key: 'ArrowUp' })
    fireEvent.keyDown(pop, { key: 'ArrowUp' })
    expect(document.activeElement).toBe(items[items.length - 1])
    fireEvent.keyDown(pop, { key: 'Home' })
    fireEvent.keyDown(pop, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(item(pop, 'Video'))
    // Enter on a button is a native click; jsdom does not synthesise it, so the click stands in.
    fireEvent.keyDown(pop, { key: 'Enter' })
    fireEvent.click(document.activeElement!)
    expect(props.onAddVideo).toHaveBeenCalledTimes(1)
    expect(pop.hidden).toBe(true)
    expect(document.activeElement).toBe(trigger)

    fireEvent.click(trigger)
    expect(pop.hidden).toBe(false)
    fireEvent.keyDown(pop, { key: 'Escape' })
    expect(pop.hidden).toBe(true)
    expect(document.activeElement).toBe(trigger)
  })

  it('is drawn on the menu surface tokens with no hard-coded colour', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/thread.css'), 'utf8')
    const rules = css.split('\n').filter(line => line.startsWith('.strip-more-pop')).join('\n')
    expect(rules).toContain('background: var(--menu-bg)')
    expect(rules).toContain('border-color: var(--menu-line)')
    expect(rules).toContain('var(--shell-bg-active)')
    expect(rules).toContain('.menu-row-d')
    expect(rules).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})

describe('the composer after the move', () => {
  it('shows no video button, keeps the attach button, and exposes both pickers', async () => {
    const invoke = vi.fn(async () => [{ path: '/tmp/clip.mp4', name: 'clip.mp4' }])
    ;(window as any).ipc = { invoke }
    const ref = React.createRef<ComposerPickers>()
    try {
      render(<AnimatedChatInput value="" setValue={vi.fn()} onSendMessage={vi.fn()} isLoading={false} pickersRef={ref} />)
      expect(screen.queryByRole('button', { name: /video/i })).toBeNull()
      expect(screen.getByRole('button', { name: 'Resim Ekle' })).toBeTruthy()

      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      const click = vi.spyOn(input, 'click').mockImplementation(() => {})
      ref.current!.pickImage()
      expect(click).toHaveBeenCalledTimes(1)

      await act(async () => { ref.current!.pickVideo() })
      expect(invoke).toHaveBeenCalledWith('open-video-dialog')
      await waitFor(() => expect(screen.getByText('clip.mp4')).toBeTruthy())
    } finally {
      delete (window as any).ipc
    }
  })
})
