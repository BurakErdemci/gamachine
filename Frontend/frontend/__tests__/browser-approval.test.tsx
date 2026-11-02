import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BrowserApproval, browserCardInfo, formatBytes } from '../renderer/components/home/BrowserApproval'

afterEach(cleanup)

const PARAMS = {
  risk_reason: 'Submits a form that contains a password field',
  element_name: 'Sign in',
  page_url: 'https://assets.example.com/login?next=/cart',
  value_preview: '',
}

describe('BrowserApproval', () => {
  it('shows the action, the element, the full page URL with its host emphasised and the risk reason', () => {
    render(<BrowserApproval info={browserCardInfo('browser_click', PARAMS)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('browser-approval')).toBeTruthy()
    expect(screen.getByTestId('bp-card-risk').textContent).toContain(PARAMS.risk_reason)
    expect(screen.getByTestId('bp-card-host').textContent).toBe('assets.example.com')
    expect(screen.getByTestId('bp-card-host').tagName).toBe('B')
    expect(screen.getByTestId('bp-card-url').textContent).toBe(PARAMS.page_url)
    expect(screen.getByTestId('bp-card-element').textContent).toBe('Sign in')
    expect(screen.getByTestId('bp-card-info').textContent).toContain('click')
    expect(screen.queryByTestId('bp-card-value')).toBeNull()
    expect(screen.queryByTestId('bp-card-warn')).toBeNull()
  })

  it('reads only the backend fields: spoofable fallbacks are ignored', () => {
    const info = browserCardInfo('browser_fill', { host: 'evil.example', page_host: 'evil.example', url: 'https://evil.example/', element: 'Trusted', name: 'Trusted' })
    expect(info).toMatchObject({ element: '', pageUrl: '', valuePreview: '', action: 'fill' })
  })

  it('shows the value preview for fill and select only', () => {
    const fill = { ...PARAMS, value_preview: 'walker@example.com' }
    render(<BrowserApproval info={browserCardInfo('browser_fill', fill)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-value').textContent).toBe('walker@example.com')
    cleanup()
    render(<BrowserApproval info={browserCardInfo('browser_select', fill)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-value')).toBeTruthy()
    cleanup()
    render(<BrowserApproval info={browserCardInfo('browser_click', fill)} onAnswer={vi.fn()} />)
    expect(screen.queryByTestId('bp-card-value')).toBeNull()
  })

  it('shows an unparseable page URL raw with a warning', () => {
    render(<BrowserApproval info={browserCardInfo('browser_click', { ...PARAMS, page_url: 'not a url' })} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-url').textContent).toBe('not a url')
    expect(screen.getByTestId('bp-card-warn')).toBeTruthy()
    expect(screen.queryByTestId('bp-card-host')).toBeNull()
  })

  it('survives malformed params', () => {
    render(<BrowserApproval info={browserCardInfo('browser_click', null)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-risk')).toBeTruthy()
  })

  it('approve and reject answer the gate', () => {
    const onAnswer = vi.fn()
    render(<BrowserApproval info={browserCardInfo('browser_click', PARAMS)} onAnswer={onAnswer} />)
    fireEvent.click(screen.getByTestId('bp-card-approve'))
    expect(onAnswer).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByTestId('bp-card-reject'))
    expect(onAnswer).toHaveBeenLastCalledWith(false)
  })

  it('shows a download card with the file, its size and the site in bold, from the backend fields only', () => {
    const params = { filename: 'theme.mp3', bytes: 3_250_000, host: 'cdn.files.example', page_url: 'https://files.example/x',
      risky: true, risk_reason: 'the agent downloads theme.mp3 from cdn.files.example', element_name: 'spoof', value_preview: 'spoof' }
    render(<BrowserApproval info={browserCardInfo('browser_download', params)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-file').textContent).toBe('theme.mp3')
    expect(screen.getByTestId('bp-card-size').textContent).toBe('3.1 MB')
    expect(screen.getByTestId('bp-card-host').tagName).toBe('B')
    expect(screen.getByTestId('bp-card-host').textContent).toBe('cdn.files.example')
    expect(screen.getByTestId('bp-card-risk').textContent).toContain('the agent downloads theme.mp3')
    expect(screen.queryByTestId('bp-card-element')).toBeNull()
    expect(screen.queryByTestId('bp-card-value')).toBeNull()
    cleanup()
    render(<BrowserApproval info={browserCardInfo('browser_download', { ...params, bytes: 'big', filename: 'a‮gpj.exe' })} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('bp-card-size').textContent).not.toMatch(/\d/)
    expect(screen.getByTestId('bp-card-file').textContent).not.toContain('‮')
  })

  it('formats sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(50 * 1024 * 1024)).toBe('50 MB')
  })
})
