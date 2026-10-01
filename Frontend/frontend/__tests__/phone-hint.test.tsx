/**
 * "Or approve on your phone" appears only on cards a paired phone can actually decide (P2 audit).
 * The phone answers registry cards (backend agentic/cards.py: mcp, mail, command, question) and
 * lists unowned ones too; a question only when it is one single-select question
 * (remote/chats.py single_choice_options). The chat flow's own create / delete / diff cards write
 * through IPC on this machine and are not registry cards, so they carry no hint.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

import { QuestionApproval, phoneCanAnswer } from '../renderer/components/home/QuestionApproval'
import { McpUnknownTray } from '../renderer/components/home/McpUnknownTray'
import { DiffViewer } from '../renderer/components/home/DiffViewer'
import { translations } from '../renderer/lib/i18n'

afterEach(cleanup)

const tr = translations.tr
const single = [{ question: 'Which one?', options: [{ label: 'A' }, { label: 'B' }] }]
const multi = [{ question: 'Which ones?', multiSelect: true, options: [{ label: 'A' }, { label: 'B' }] }]

describe('phone hint', () => {
  it('a single-select question says the phone can answer it', () => {
    expect(phoneCanAnswer(single)).toBe(true)
    render(<QuestionApproval questions={single} onSubmit={vi.fn()} phonePaired />)
    expect(screen.getByText(tr['card.phoneHintAnswer'])).toBeTruthy()
  })

  it('a multi-select or multi-question card does not promise the phone', () => {
    expect(phoneCanAnswer(multi)).toBe(false)
    expect(phoneCanAnswer([...single, ...single])).toBe(false)
    render(<QuestionApproval questions={multi} onSubmit={vi.fn()} phonePaired />)
    expect(screen.queryByText(tr['card.phoneHintAnswer'])).toBeNull()
  })

  it('no paired phone, no hint', () => {
    render(<QuestionApproval questions={single} onSubmit={vi.fn()} />)
    expect(screen.queryByText(tr['card.phoneHintAnswer'])).toBeNull()
  })

  it('the unknown-source tray carries the hint (the phone lists unowned cards)', () => {
    const gates: any[] = [{ gateId: 'g1', tool: 'manage_scene', params: {}, workspacePath: null }]
    render(<McpUnknownTray gates={gates} apiBase="http://x" sessionToken="t" showToast={vi.fn()} phonePaired />)
    expect(screen.getByText(tr['card.phoneHint'])).toBeTruthy()
  })

  it('the gate diff carries the hint, the chat flow diff does not', () => {
    const data = { original_code: 'a', fixed_code: 'b', explanation: 'x' }
    render(<DiffViewer diffData={data} onAccept={vi.fn()} onReject={vi.fn()} phonePaired />)
    expect(screen.getByTestId('diff-phone-hint')).toBeTruthy()
    cleanup()
    render(<DiffViewer diffData={data} onAccept={vi.fn()} onReject={vi.fn()} />)
    expect(screen.queryByTestId('diff-phone-hint')).toBeNull()
  })
})
