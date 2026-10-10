import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor, within, act } from '@testing-library/react'

import { AgentGuidePage, __resetAgentGuideCacheForTests } from '../renderer/components/home/settings/AgentGuidePage'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const sections = [
  { id: 'ALWAYS', when: 'always', text: 'Always rule\nSecond line' },
  { id: 'PROJECT', when: 'project_open', text: 'Project rule' },
  { id: 'UNITY_CONNECTED', when: 'connected', text: 'Connected rule' },
  { id: 'UNITY_OFF', when: 'off', text: 'Off rule' },
  { id: 'UNITY_NOT_RESPONDING', when: 'not_responding', text: 'Not responding rule' },
] as const

const base = () => ({
  lang: 'tr' as const,
  API: 'http://localhost:8000',
  token: 'session-token',
  http: {
    get: vi.fn().mockResolvedValue({ data: { language: 'tr', sections, addendum: 'My rule' } }),
    put: vi.fn().mockResolvedValue({ data: { addendum: 'Saved rule' } }),
  },
  saved: vi.fn(),
})

const loaded = () => screen.findByTestId('agent-guide-addendum')

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

beforeEach(() => { aktifDilAyarla('tr') })
afterEach(() => { cleanup(); __resetAgentGuideCacheForTests(); aktifDilAyarla('tr') })

describe('agent guide settings', () => {
  it('renders the five read-only sections in server order with their situation labels', async () => {
    const props = base()
    const { container } = render(<AgentGuidePage {...props} />)
    await loaded()
    expect(props.http.get).toHaveBeenCalledWith(`${props.API}/agent-guide?lang=tr`, {
      headers: { 'X-Session-Token': props.token },
    })
    const blocks = Array.from(container.querySelectorAll('[data-testid^="agent-guide-section-"]'))
    expect(blocks.map(b => b.getAttribute('data-testid'))).toEqual(
      sections.map(s => `agent-guide-section-${s.id}`),
    )
    sections.forEach((section, index) => {
      expect(within(blocks[index] as HTMLElement).getByText(cevir(`set.ajan.when.${section.when}`))).toBeTruthy()
      expect(blocks[index].textContent).toContain(section.text)
      expect(blocks[index].querySelector('input, textarea, button, [contenteditable]')).toBeNull()
    })
  })

  it('loads the addendum, counts characters, and enables saving only for a trimmed change', async () => {
    render(<AgentGuidePage {...base()} />)
    const textarea = await loaded() as HTMLTextAreaElement
    const button = screen.getByTestId('agent-guide-save') as HTMLButtonElement
    expect(textarea.value).toBe('My rule')
    expect(textarea.rows).toBe(8)
    expect(textarea.maxLength).toBe(4000)
    expect(textarea.getAttribute('aria-label')).toBe(cevir('set.ajan.addendum'))
    expect(screen.getByTestId('agent-guide-count').textContent).toBe('7 / 4000')
    expect(button.disabled).toBe(true)
    fireEvent.change(textarea, { target: { value: ' My rule  ' } })
    expect(button.disabled).toBe(true)
    expect(screen.getByTestId('agent-guide-count').textContent).toBe('10 / 4000')
    fireEvent.change(textarea, { target: { value: 'New rule' } })
    expect(button.disabled).toBe(false)
    expect(screen.getByTestId('agent-guide-count').textContent).toBe('8 / 4000')
    expect(button.textContent).toBe(cevir('set.ajan.save'))
    expect(button.textContent).not.toBe(cevir('settings.save'))
  })

  it('sends the draft and session header, then adopts the server value and reports saved', async () => {
    const props = base()
    let finish!: (value: { data: { addendum: string } }) => void
    props.http.put.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    render(<AgentGuidePage {...props} />)
    const textarea = await loaded() as HTMLTextAreaElement
    const button = screen.getByTestId('agent-guide-save') as HTMLButtonElement
    fireEvent.change(textarea, { target: { value: ' New rule ' } })
    fireEvent.click(button)
    expect(button.disabled).toBe(true)
    expect(textarea.disabled).toBe(true)
    expect(props.http.put).toHaveBeenCalledWith(`${props.API}/agent-guide/addendum`, { text: ' New rule ' }, {
      headers: { 'X-Session-Token': props.token },
    })
    finish({ data: { addendum: 'Saved rule' } })
    await waitFor(() => expect(textarea.value).toBe('Saved rule'))
    expect(props.saved).toHaveBeenCalledTimes(1)
    expect(textarea.disabled).toBe(false)
    expect(button.disabled).toBe(true)
    expect(screen.getByTestId('agent-guide-count').textContent).toBe('10 / 4000')
  })

  it('keeps the draft and shows an alert when saving fails', async () => {
    const props = base()
    props.http.put.mockRejectedValue(new Error('save failed'))
    render(<AgentGuidePage {...props} />)
    const textarea = await loaded() as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Keep this draft' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    expect((await screen.findByRole('alert')).textContent).toBe(cevir('set.ajan.saveError'))
    expect(textarea.value).toBe('Keep this draft')
    expect(props.saved).not.toHaveBeenCalled()
    expect((screen.getByTestId('agent-guide-save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('treats a missing put method as a save failure', async () => {
    const props = base()
    render(<AgentGuidePage {...props} http={{ get: props.http.get }} />)
    const textarea = await loaded() as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'Keep this draft' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    expect((await screen.findByRole('alert')).textContent).toBe(cevir('set.ajan.saveError'))
    expect(textarea.value).toBe('Keep this draft')
    expect(props.saved).not.toHaveBeenCalled()
  })

  it('shows a load error and refetches when retry is clicked', async () => {
    const props = base()
    props.http.get.mockRejectedValueOnce(new Error('load failed'))
    render(<AgentGuidePage {...props} />)
    expect(screen.getByText(cevir('set.ajan.loading'))).toBeTruthy()
    const retry = await screen.findByRole('button', { name: cevir('set.ajan.retry') })
    expect(screen.getByText(cevir('set.ajan.loadError'))).toBeTruthy()
    fireEvent.click(retry)
    await loaded()
    expect(props.http.get).toHaveBeenCalledTimes(2)
    expect(props.http.get).toHaveBeenLastCalledWith(`${props.API}/agent-guide?lang=tr`, {
      headers: { 'X-Session-Token': props.token },
    })
  })

  it('shows only the heading and unavailable note without API or http and makes no request', () => {
    const props = base()
    const { rerender } = render(<AgentGuidePage {...props} API={undefined} />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(cevir('set.nav.ajan'))
    expect(screen.getByText(cevir('set.ajan.unavailable'))).toBeTruthy()
    expect(screen.queryByTestId('agent-guide-addendum')).toBeNull()
    expect(props.http.get).not.toHaveBeenCalled()
    rerender(<AgentGuidePage {...props} http={undefined} />)
    expect(screen.getByText(cevir('set.ajan.unavailable'))).toBeTruthy()
    expect(props.http.get).not.toHaveBeenCalled()
  })

  it('requests the English guide when mounted in English and when the language changes', async () => {
    const props = base()
    aktifDilAyarla('en')
    const { rerender } = render(<AgentGuidePage {...props} lang="en" />)
    await loaded()
    expect(props.http.get).toHaveBeenCalledWith(`${props.API}/agent-guide?lang=en`, {
      headers: { 'X-Session-Token': props.token },
    })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Agent')
    aktifDilAyarla('tr')
    rerender(<AgentGuidePage {...props} lang="tr" />)
    await waitFor(() => expect(props.http.get).toHaveBeenLastCalledWith(`${props.API}/agent-guide?lang=tr`, {
      headers: { 'X-Session-Token': props.token },
    }))
    await loaded()
  })

  it('clears the addendum with an empty draft and uses an empty header without a token', async () => {
    const props = base()
    props.http.put.mockResolvedValue({ data: { addendum: '' } })
    render(<AgentGuidePage {...props} token={undefined} />)
    const textarea = await loaded() as HTMLTextAreaElement
    expect(props.http.get).toHaveBeenCalledWith(`${props.API}/agent-guide?lang=tr`, {
      headers: { 'X-Session-Token': '' },
    })
    fireEvent.change(textarea, { target: { value: '' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    await waitFor(() => expect(props.saved).toHaveBeenCalledTimes(1))
    expect(props.http.put).toHaveBeenCalledWith(`${props.API}/agent-guide/addendum`, { text: '' }, {
      headers: { 'X-Session-Token': '' },
    })
    expect(textarea.value).toBe('')
    expect(screen.getByTestId('agent-guide-count').textContent).toBe('0 / 4000')
    expect((screen.getByTestId('agent-guide-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('issues exactly one GET for a single mount', async () => {
    const props = base()
    render(<AgentGuidePage {...props} />)
    await loaded()
    await act(async () => {})
    expect(props.http.get).toHaveBeenCalledTimes(1)
  })

  it('keeps an unsaved draft across remount and a language change', async () => {
    const props = base()
    const first = render(<AgentGuidePage {...props} />)
    fireEvent.change(await loaded(), { target: { value: 'Unsaved rule' } })
    first.unmount()
    const second = render(<AgentGuidePage {...props} />)
    expect((await loaded() as HTMLTextAreaElement).value).toBe('Unsaved rule')
    expect((screen.getByTestId('agent-guide-save') as HTMLButtonElement).disabled).toBe(false)
    second.rerender(<AgentGuidePage {...props} lang="en" />)
    expect((await loaded() as HTMLTextAreaElement).value).toBe('Unsaved rule')
    expect(props.http.get).toHaveBeenCalledTimes(3)
  })

  it('waits for an unmounted save before refetching and does not report saved from that instance', async () => {
    const props = base()
    const pending = deferred<{ data: { addendum: string } }>()
    props.http.put.mockReturnValue(pending.promise)
    const first = render(<AgentGuidePage {...props} />)
    fireEvent.change(await loaded(), { target: { value: ' New rule ' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    first.unmount()
    render(<AgentGuidePage {...props} />)
    expect(screen.getByText(cevir('set.ajan.loading'))).toBeTruthy()
    await act(async () => {})
    expect(props.http.get).toHaveBeenCalledTimes(1)
    props.http.get.mockResolvedValue({ data: { sections, addendum: 'New rule' } })
    await act(async () => { pending.resolve({ data: { addendum: 'New rule' } }) })
    expect((await loaded() as HTMLTextAreaElement).value).toBe('New rule')
    expect((screen.getByTestId('agent-guide-save') as HTMLButtonElement).disabled).toBe(true)
    expect(props.http.get).toHaveBeenCalledTimes(2)
    expect(props.saved).not.toHaveBeenCalled()
  })

  it('refetches after an unmounted save rejects and keeps the unsaved draft', async () => {
    const props = base()
    const pending = deferred<{ data: { addendum: string } }>()
    props.http.put.mockReturnValue(pending.promise)
    const first = render(<AgentGuidePage {...props} />)
    fireEvent.change(await loaded(), { target: { value: 'Keep draft' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    first.unmount()
    render(<AgentGuidePage {...props} />)
    await act(async () => { pending.reject(new Error('save failed')) })
    expect((await loaded() as HTMLTextAreaElement).value).toBe('Keep draft')
    expect(props.http.get).toHaveBeenCalledTimes(2)
    expect(props.saved).not.toHaveBeenCalled()
  })

  it('waits for a pending save during a language change and finishes with the server value', async () => {
    const props = base()
    const pending = deferred<{ data: { addendum: string } }>()
    props.http.put.mockReturnValue(pending.promise)
    const { rerender } = render(<AgentGuidePage {...props} />)
    fireEvent.change(await loaded(), { target: { value: 'New rule' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    rerender(<AgentGuidePage {...props} lang="en" />)
    await act(async () => {})
    expect(props.http.get).toHaveBeenCalledTimes(1)
    props.http.get.mockResolvedValue({ data: { sections, addendum: 'New rule' } })
    await act(async () => { pending.resolve({ data: { addendum: 'New rule' } }) })
    expect((await loaded() as HTMLTextAreaElement).value).toBe('New rule')
    expect((screen.getByTestId('agent-guide-save') as HTMLButtonElement).disabled).toBe(true)
    expect(props.saved).toHaveBeenCalledTimes(1)
  })

  it.each([
    undefined, '<html>', {}, { sections: [], addendum: 42 },
    { sections: {}, addendum: '' }, { sections: [null], addendum: '' },
    ...['id', 'when', 'text'].map(key => ({ sections: [{ id: 'A', when: 'always', text: 'Rule', [key]: 42 }], addendum: '' })),
  ])('shows a load error for malformed GET data %j', async data => {
    const props = base()
    props.http.get.mockResolvedValue({ data })
    render(<AgentGuidePage {...props} />)
    expect(await screen.findByRole('button', { name: cevir('set.ajan.retry') })).toBeTruthy()
    expect(screen.getByText(cevir('set.ajan.loadError'))).toBeTruthy()
    expect(screen.queryByTestId('agent-guide-addendum')).toBeNull()
  })

  it.each([undefined, '<html>', {}, { addendum: null }, { addendum: 42 }])(
    'keeps the draft and shows an alert for malformed PUT data %j', async data => {
      const props = base()
      props.http.put.mockResolvedValue({ data })
      const first = render(<AgentGuidePage {...props} />)
      const textarea = await loaded() as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'Keep draft' } })
      fireEvent.click(screen.getByTestId('agent-guide-save'))
      expect((await screen.findByRole('alert')).textContent).toBe(cevir('set.ajan.saveError'))
      expect(textarea.value).toBe('Keep draft')
      expect(props.saved).not.toHaveBeenCalled()
      first.unmount()
      render(<AgentGuidePage {...props} />)
      expect((await loaded() as HTMLTextAreaElement).value).toBe('Keep draft')
    },
  )

  it('uses the raw situation as the label for an unknown when value', async () => {
    const props = base()
    props.http.get.mockResolvedValue({ data: { sections: [{ id: 'X', when: 'future_state', text: 'Rule' }], addendum: '' } })
    render(<AgentGuidePage {...props} />)
    await loaded()
    expect(within(screen.getByTestId('agent-guide-section-X')).getByText('future_state')).toBeTruthy()
  })

  it('ignores a stale GET that finishes after the language changes', async () => {
    const props = base()
    const oldRequest = deferred<{ data: { sections: typeof sections; addendum: string } }>()
    props.http.get.mockReturnValueOnce(oldRequest.promise)
    props.http.get.mockResolvedValue({ data: { sections, addendum: 'English rule' } })
    const { rerender } = render(<AgentGuidePage {...props} />)
    rerender(<AgentGuidePage {...props} lang="en" />)
    expect((await loaded() as HTMLTextAreaElement).value).toBe('English rule')
    await act(async () => { oldRequest.resolve({ data: { sections, addendum: 'Stale rule' } }) })
    expect((screen.getByTestId('agent-guide-addendum') as HTMLTextAreaElement).value).toBe('English rule')
    expect(props.http.get).toHaveBeenCalledTimes(2)
  })

  it('clears a save error when the draft is edited', async () => {
    const props = base()
    props.http.put.mockRejectedValue(new Error('save failed'))
    render(<AgentGuidePage {...props} />)
    const textarea = await loaded()
    fireEvent.change(textarea, { target: { value: 'New rule' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    await screen.findByRole('alert')
    fireEvent.change(textarea, { target: { value: 'Another rule' } })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows loading on refetch and clears a save error after refetch', async () => {
    const props = base()
    props.http.put.mockRejectedValue(new Error('save failed'))
    const { rerender } = render(<AgentGuidePage {...props} />)
    fireEvent.change(await loaded(), { target: { value: 'Keep draft' } })
    fireEvent.click(screen.getByTestId('agent-guide-save'))
    await screen.findByRole('alert')
    const pending = deferred<{ data: { sections: typeof sections; addendum: string } }>()
    props.http.get.mockReturnValueOnce(pending.promise)
    rerender(<AgentGuidePage {...props} lang="en" />)
    expect(screen.getByText(cevir('set.ajan.loading'))).toBeTruthy()
    expect(screen.queryByTestId('agent-guide-addendum')).toBeNull()
    await act(async () => { pending.resolve({ data: { sections, addendum: 'My rule' } }) })
    await loaded()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
