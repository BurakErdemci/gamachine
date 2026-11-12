import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { holdBackPartialMarkdown as h } from '../renderer/lib/streamingMarkdown'

const HOME = resolve(__dirname, '../renderer/components/home')

describe('holdBackPartialMarkdown', () => {
  it('keeps complete text untouched', () => {
    for (const s of [
      'Updated.\n\nSee **[Tune.cs](file:///C:/A%20B/Tune.cs)** now.',
      'plain text', 'a [x](y) b', '**bold** and _it_', 'a (paren) [b]: c', '', 'line\n',
      '![img](file:///C:/a.png)', '- [x] done',
    ]) expect(h(s)).toBe(s)
  })
  it('reduces an unfinished link to its label', () => {
    expect(h('See [Tune.cs](file:///C:/a/b')).toBe('See Tune.cs')
    expect(h('See [Tune.cs](')).toBe('See Tune.cs')
    expect(h('See [Tune.cs]')).toBe('See Tune.cs')
    expect(h('See [Tune.c')).toBe('See Tune.c')
    expect(h('Shot ![scene](file:///C:/a')).toBe('Shot scene')
  })
  it('drops emphasis that opens an unfinished link', () => {
    expect(h('See **[Tune.cs](file:///C:/a')).toBe('See Tune.cs')
    expect(h('See _[Tune.cs](file:///C:/a')).toBe('See Tune.cs')
  })
  it('handles parentheses inside a URL', () => {
    expect(h('a [f](file:///C:/x(1).cs) b')).toBe('a [f](file:///C:/x(1).cs) b')
    expect(h('a [f](file:///C:/x(1).cs')).toBe('a f')
  })
  it('only touches the last line, after earlier finished links', () => {
    expect(h('[a](b)\n\nnext [c](d')).toBe('[a](b)\n\nnext c')
    expect(h('[a](b) then [c](d')).toBe('[a](b) then c')
  })
  it('leaves an open code fence alone', () => {
    const s = 'x\n\n```cs\nvar a = arr[i](b'
    expect(h(s)).toBe(s)
  })
  it('leaves inline code alone', () => {
    expect(h('use `arr[i](x` here')).toBe('use `arr[i](x` here')
    expect(h('use `arr[i](x')).toBe('use `arr[i](x')
  })
  it('drops a lone unmatched bold opener on the last line', () => {
    expect(h('so **Tune')).toBe('so Tune')
    expect(h('**a** and **b')).toBe('**a** and b')
    expect(h('**[T](file:///C:/a.cs)')).toBe('[T](file:///C:/a.cs)')
  })
})

describe('the streamed message is the one marked as streaming', () => {
  it('ChatPanel marks only the last message while the turn runs', () => {
    expect(readFileSync(resolve(HOME, 'ChatPanel.tsx'), 'utf8'))
      .toContain('streaming={loading && msgIdx === messages.length - 1}')
  })
  it('the side chat marks an answer until it finished or failed', () => {
    expect(readFileSync(resolve(HOME, 'SideChatPanel.tsx'), 'utf8'))
      .toContain('streaming={!m.finished && !m.failed}')
  })
})
