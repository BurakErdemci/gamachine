/**
 * `@<id>` chat mentions: the user names the target chat by its stable number,
 * because titles repeat (a chat's title is its first message) and the AI
 * would otherwise guess (Burak, 27 Sep 2026).
 *
 * Measured here: the composer's `@` menu (who is listed, filtering, keyboard
 * and mouse picks, Esc, Enter still sending), the `#<id>` next to each chat in
 * the sidebar and the tabs, and the chip in the user's own bubble.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: 'idle', elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { ChatTabs } from '../renderer/components/home/ChatTabs'
import { MarkdownRenderer } from '../renderer/components/home/MarkdownRenderer'
import {
  mentionQueryAt, mentionTargets, foldForSearch, findMentions, mentionLabel, MENTION_LABEL_MAX,
} from '../renderer/lib/chatMentions'
import { cevir } from '../renderer/lib/i18n'

const conv = (id: number, title: string, extra: object = {}) => ({
  id, title, created_at: `2026-09-27T00:00:${String(id).padStart(2, '0')}Z`,
  updated_at: '2026-09-27T00:00:00Z', parent_id: null, hidden: false, ...extra,
})

// 7 is the chat on screen; 20 a side row (never listed by the backend, guarded anyway).
const CHATS = [
  conv(12, 'Selam'),
  conv(15, 'UI dalı', { parent_id: 12, hidden: true }),
  conv(7, 'Selam'),
  conv(20, 'Yan soru', { side_of: 12 }),
  conv(31, 'İstanbul planı'),
  conv(120, 'Başka'),
]

afterEach(() => cleanup())

const mount = (chats = CHATS, currentChatId: number | null = 7) => {
  const setValue = vi.fn()
  const onSendMessage = vi.fn()
  render(
    <AnimatedChatInput
      value="" setValue={setValue} onSendMessage={onSendMessage} isLoading={false}
      api="http://127.0.0.1:1" chats={chats as any} currentChatId={currentChatId}
    />,
  )
  const textarea = screen.getByRole('textbox') as HTMLTextAreaElement
  const type = (value: string, caret = value.length) => {
    fireEvent.change(textarea, { target: { value } })
    textarea.setSelectionRange(caret, caret)
    fireEvent.select(textarea)
  }
  return { setValue, onSendMessage, textarea, type }
}

const listed = () =>
  Array.from(document.querySelectorAll('[data-testid^="mention-option-"]'))
    .map(el => Number(el.getAttribute('data-testid')!.replace('mention-option-', '')))

const menuGone = () => waitFor(() => expect(screen.queryByTestId('mention-menu')).toBeNull())

describe('composer @ menu', () => {
  it('opens on @ and lists chats and branches, without the current chat or side rows', () => {
    const { type } = mount()
    expect(screen.queryByTestId('mention-menu')).toBeNull()
    type('@')
    expect(screen.getByTestId('mention-menu')).toBeTruthy()
    expect(listed()).toEqual([12, 15, 31, 120])
  })

  it('opens only at the start or after whitespace', () => {
    const { type } = mount()
    type('mail@1')
    expect(screen.queryByTestId('mention-menu')).toBeNull()
    type('selam @1')
    expect(screen.getByTestId('mention-menu')).toBeTruthy()
  })

  it('filters by number prefix, the exact number first', () => {
    const { type } = mount()
    type('@1')
    expect(listed()).toEqual([12, 15, 120])
    type('@12')
    expect(listed()).toEqual([12, 120])
    type('@120')
    expect(listed()).toEqual([120])
  })

  it('filters by title, case-insensitive and Turkish-safe', () => {
    const { type } = mount()
    type('@sel')
    expect(listed()).toEqual([12])
    type('@istanbul')
    expect(listed()).toEqual([31])
    type('@İSTANBUL')
    expect(listed()).toEqual([31])
    type('@dalı')
    expect(listed()).toEqual([15])
  })

  it('shows a branch with its own name, a branch hint and its parent', () => {
    const { type } = mount()
    type('@')
    const option = screen.getByTestId('mention-option-15')
    expect(option.textContent).toContain('UI dalı')
    expect(option.textContent).toContain(cevir('mention.branch'))
    expect(option.textContent).toContain(cevir('mention.branchOf', { no: 12, ad: 'Selam' }))
    expect(screen.getByTestId('mention-option-12').textContent).not.toContain(cevir('mention.branch'))
  })

  it('Arrow keys and Enter pick, replacing the typed @query', async () => {
    const { type, textarea, setValue, onSendMessage } = mount()
    type('bunu @s')
    // "Selam" and "İstanbul planı"; "Başka" has an `ş`, not an `s`.
    expect(listed()).toEqual([12, 31])
    fireEvent.keyDown(textarea, { key: 'ArrowDown' })
    fireEvent.keyDown(textarea, { key: 'ArrowDown' })
    fireEvent.keyDown(textarea, { key: 'ArrowUp' })
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(textarea.value).toBe('bunu @31 ')
    expect(setValue).toHaveBeenLastCalledWith('bunu @31 ')
    expect(onSendMessage).not.toHaveBeenCalled()
    await menuGone()
  })

  it('Tab picks too, and a pick mid-text reuses the following space', async () => {
    const { type, textarea } = mount()
    type('önce @1 sonra', 7)
    expect(screen.getByTestId('mention-menu')).toBeTruthy()
    fireEvent.keyDown(textarea, { key: 'Tab' })
    expect(textarea.value).toBe('önce @12 sonra')
    await menuGone()
  })

  it('a click picks', async () => {
    const { type, textarea } = mount()
    type('@')
    fireEvent.click(screen.getByTestId('mention-option-15'))
    expect(textarea.value).toBe('@15 ')
    await menuGone()
  })

  it('Esc closes the menu, and Enter then sends', async () => {
    const { type, textarea, onSendMessage } = mount()
    type('@1')
    fireEvent.keyDown(textarea, { key: 'Escape' })
    await menuGone()
    expect(textarea.value).toBe('@1')
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledWith('@1', [], [])
  })

  it('Enter sends when no menu is open', () => {
    const { type, textarea, onSendMessage } = mount()
    type('selam dünya')
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledWith('selam dünya', [], [])
  })

  it('Enter sends when the @query matches nothing', () => {
    const { type, textarea, onSendMessage } = mount()
    type('@999')
    expect(screen.queryByTestId('mention-menu')).toBeNull()
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledWith('@999', [], [])
  })

  it('the slash palette still works and does not open the @ menu', () => {
    const { type } = mount()
    type('/comp')
    expect(screen.queryByTestId('mention-menu')).toBeNull()
    expect(screen.getByText('/compact')).toBeTruthy()
  })

  // Codex mentionaudit, 27 Sep 2026: Enter that confirms an IME candidate
  // picked the highlighted chat mid-word.
  const composing = { isComposing: true, keyCode: 229 }

  it('keys an IME is composing with leave the @ menu alone', async () => {
    const { type, textarea, onSendMessage } = mount()
    type('@Se')
    expect(screen.getByTestId('mention-menu')).toBeTruthy()
    fireEvent.compositionStart(textarea)
    for (const key of ['ArrowDown', 'Tab', 'Enter', 'Escape']) {
      fireEvent.keyDown(textarea, { key, ...composing })
    }
    expect(textarea.value).toBe('@Se')
    expect(screen.getByTestId('mention-menu')).toBeTruthy()
    expect(onSendMessage).not.toHaveBeenCalled()
    // keyCode 229 alone (no isComposing) is composition too.
    fireEvent.keyDown(textarea, { key: 'Enter', keyCode: 229 })
    expect(textarea.value).toBe('@Se')
    fireEvent.compositionEnd(textarea)
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(textarea.value).toBe('@12 ')
    await menuGone()
  })

  it('keys an IME is composing with neither pick a / command nor send', () => {
    const { type, textarea, onSendMessage } = mount()
    type('/comp')
    fireEvent.keyDown(textarea, { key: 'Enter', ...composing })
    expect(textarea.value).toBe('/comp')
    expect(screen.getByText('/compact')).toBeTruthy()
    type('selam')
    fireEvent.keyDown(textarea, { key: 'Enter', ...composing })
    expect(onSendMessage).not.toHaveBeenCalled()
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledWith('selam', [], [])
  })
})

describe('mention helpers', () => {
  it('finds the @query at the caret', () => {
    expect(mentionQueryAt('@', 1)).toEqual({ start: 0, query: '' })
    expect(mentionQueryAt('a @ab', 5)).toEqual({ start: 2, query: 'ab' })
    expect(mentionQueryAt('a @ab cd', 8)).toBeNull()
    expect(mentionQueryAt('x@ab', 4)).toBeNull()
    expect(mentionQueryAt('@@a', 3)).toBeNull()
    expect(mentionQueryAt('no at here', 10)).toBeNull()
  })

  it('folds Turkish dotted and dotless i together', () => {
    expect(foldForSearch('İSTANBUL')).toBe('istanbul')
    expect(foldForSearch('ISI')).toBe('isi')
    expect(foldForSearch('Island')).toBe('island')
  })

  it('without a current chat every non-side chat is a target', () => {
    expect(mentionTargets(CHATS as any, null, '').map(m => m.id)).toEqual([12, 15, 7, 31, 120])
  })

  // Twin of LITERAL_CASES in Backend/tests/test_chat_mentions.py: a chip must
  // appear exactly where the server resolves a mention (Codex mentionaudit,
  // 27 Sep 2026). Keep the two tables identical.
  const LITERAL_CASES: [string, number[]][] = [
    ['Use the literal code `@12` in the example.', []],
    ['See https://example.invalid/docs/@12 for syntax.', []],
    ['https://a.b/@12 ve @13', [13]],
    ['ftp://h/x,@12', []],
    ['docs/@12', []],
    ['path/to/@12 ve @13', [13]],
    ['`x`@12', [12]],
    ['``a `@12` b`` @13', [13]],
    ['a `b @12', [12]],
    ['```\n@12\n```\n@13', [13]],
    ['~~~py\n@12\n~~~~\n@13', [13]],
    ['   ```\n@12\n``` \n@13', [13]],
    ['```\n@12 unclosed fence', []],
    ['```js `x`\n@12', [12]],
    ['```\n@12\n~~~\n@13', []],
    // Codex mentionverify, 27 Sep 2026: URIs without `//` carry `@` and queries.
    ['Open mailto:someone@example.com?subject=@12 to draft a message.', []],
    ['<MailTo:a@b.c?cc=@12> tel:+90@12 SMS:5?body=@12 ve @13', [13]],
    ['hotel:@12 sms: @13', [12, 13]],
  ]

  it.each(LITERAL_CASES)('skips code and URLs like the backend: %j', (text, ids) => {
    expect(findMentions(text).map(m => m.id)).toEqual(ids)
    for (const m of findMentions(text)) expect(text.slice(m.index, m.index + m.text.length)).toBe(m.text)
  })
})

const noop = () => {}

describe('#<id> on screen', () => {
  it('the sidebar shows each chat number', () => {
    render(
      <Sidebar
        {...({} as any)}
        isSidebarOpen sidebarTab="chats" setSidebarTab={noop}
        conversations={[conv(12, 'Selam'), conv(7, 'Selam')] as any} activeConvId={7}
        selectConversation={noop} createNewConversation={noop} deleteConversation={noop}
        editingId={null} setEditingId={noop} tempTitle="" setTempTitle={noop} saveRename={noop}
        fileTree={[]} treeContextMenu={null} setTreeContextMenu={noop}
        user={{ id: 1, name: 'b', sessionToken: 't' }} setShowSettings={noop} handleLogout={noop}
      />,
    )
    expect(screen.getByTestId('conv-number-12').textContent).toBe('#12')
    expect(screen.getByTestId('conv-number-7').textContent).toBe('#7')
    expect(screen.getByTestId('conv-number-12').getAttribute('title')).toBe(cevir('mention.chatNumber', { no: 12 }))
  })

  it('the tabs show each chat number', () => {
    render(
      <ChatTabs
        conversations={[conv(12, 'Selam'), conv(15, 'UI dalı', { parent_id: 12 })] as any}
        activeConvId={12} branchBlocked={false} onSelect={noop}
        onBranch={async () => null} onClose={noop}
      />,
    )
    expect(screen.getByTestId('tab-number-12').textContent).toBe('#12')
    expect(screen.getByTestId('tab-number-15').textContent).toBe('#15')
  })
})

describe('mention chip in a user bubble', () => {
  const titles = new Map([[12, 'Selam']])
  const chipTexts = () => Array.from(document.querySelectorAll('[data-mention]')).map(c => c.textContent)

  it('shows a known chat by its title, the number on hover; an unknown one stays @<id>', () => {
    render(<MarkdownRenderer content="bak @12 ve @99, mail a@12.com @12abc" mentionTitles={titles} />)
    const chips = Array.from(document.querySelectorAll('[data-mention]'))
    expect(chips.map(c => c.textContent)).toEqual(['@Selam', '@99'])
    expect(chips.map(c => c.getAttribute('data-mention'))).toEqual(['12', '99'])
    expect(chips[0].hasAttribute('data-mention-known')).toBe(true)
    expect(chips[0].getAttribute('title')).toBe('#12 · Selam')
    expect(chips[1].hasAttribute('data-mention-known')).toBe(false)
    expect(chips[1].getAttribute('title')).toBe(cevir('mention.unknown'))
    expect(document.body.textContent).toContain('bak @Selam ve @99, mail a@12.com @12abc')
  })

  it('a branch shows its own title', () => {
    const fam = new Map([[12, 'Selam'], [15, 'UI dalı']])
    render(<MarkdownRenderer content="@15 ile @12" mentionTitles={fam} />)
    expect(chipTexts()).toEqual(['@UI dalı', '@Selam'])
  })

  it('cuts a long title with an ellipsis, the full title on hover', () => {
    const long = 'Oyuncu hareketi ve kamera takibi için ayrıntılı plan'
    render(<MarkdownRenderer content="@31" mentionTitles={new Map([[31, long]])} />)
    const chip = document.querySelector('[data-mention="31"]')!
    expect(chip.textContent).toBe(`@${mentionLabel(31, long)}`)
    expect(mentionLabel(31, long)).toMatch(/…$/)
    expect(Array.from(mentionLabel(31, long)).length).toBeLessThanOrEqual(MENTION_LABEL_MAX)
    expect(chip.getAttribute('title')).toBe(`#31 · ${long}`)
  })

  it('a chat with a blank title keeps @<id>, never an empty chip', () => {
    render(<MarkdownRenderer content="@40" mentionTitles={new Map([[40, '  ']])} />)
    expect(chipTexts()).toEqual(['@40'])
    expect(document.querySelector('[data-mention="40"]')!.getAttribute('title')).toBe('#40')
  })

  it('follows a rename', () => {
    const { rerender } = render(<MarkdownRenderer content="@12" mentionTitles={titles} />)
    expect(chipTexts()).toEqual(['@Selam'])
    rerender(<MarkdownRenderer content="@12" mentionTitles={new Map([[12, 'Yeni ad']])} />)
    expect(chipTexts()).toEqual(['@Yeni ad'])
  })

  it('draws no chip without titles (assistant text, other renderers)', () => {
    render(<MarkdownRenderer content="bak @12" />)
    expect(document.querySelector('[data-mention]')).toBeNull()
  })

  it('leaves code alone', () => {
    render(<MarkdownRenderer content={'`@12` ve @12'} mentionTitles={titles} />)
    expect(chipTexts()).toEqual(['@Selam'])
    expect(document.querySelector('code')!.textContent).toBe('@12')
  })

  it('leaves URLs and paths alone', () => {
    render(<MarkdownRenderer content={'bak https://example.invalid/docs/@12, docs/@12 ve @12'} mentionTitles={titles} />)
    expect(chipTexts()).toEqual(['@Selam'])
  })
})

describe('mention label', () => {
  it('keeps a short title, cuts a long one, falls back to @<id> when blank', () => {
    expect(mentionLabel(12, 'Selam')).toBe('Selam')
    expect(mentionLabel(12, '  Selam  ')).toBe('Selam')
    expect(mentionLabel(12, 'x'.repeat(MENTION_LABEL_MAX))).toBe('x'.repeat(MENTION_LABEL_MAX))
    expect(mentionLabel(12, 'x'.repeat(MENTION_LABEL_MAX + 1))).toBe(`${'x'.repeat(MENTION_LABEL_MAX - 1)}…`)
    expect(mentionLabel(12, '')).toBe('@12')
    expect(mentionLabel(12, null)).toBe('@12')
  })

  it('never splits an emoji', () => {
    const label = mentionLabel(1, '🎮'.repeat(40))
    expect(label).toBe(`${'🎮'.repeat(MENTION_LABEL_MAX - 1)}…`)
  })
})

describe('composer mention hint', () => {
  it('names each known @<id> once under the textarea; unknown ids are left out', () => {
    const { type, textarea } = mount()
    expect(screen.queryByTestId('mention-resolved')).toBeNull()
    type('@15 ve @12, tekrar @15, yok @999 ')
    const hint = screen.getByTestId('mention-resolved')
    expect(hint.textContent).toBe('@15 → UI dalı@12 → Selam')
    expect(hint.getAttribute('aria-label')).toBe(cevir('mention.resolved'))
    expect(textarea.value).toBe('@15 ve @12, tekrar @15, yok @999 ')
  })

  it('ignores @<id> inside code, like the chips', () => {
    const { type } = mount()
    type('`@12` ')
    expect(screen.queryByTestId('mention-resolved')).toBeNull()
  })
})
