import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BrowserApproval, browserCardInfo } from '../renderer/components/home/BrowserApproval'

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
})
