import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BrowserApproval, browserCardInfo } from '../renderer/components/home/BrowserApproval'

afterEach(cleanup)

const PARAMS = {
  risk_reason: 'Submits a form that contains a password field',
  element_name: 'Sign in',
  host: 'assets.example.com',
}

describe('BrowserApproval', () => {
  it('shows the action, the element, the page host and the risk reason', () => {
    render(<BrowserApproval info={browserCardInfo('browser_click', PARAMS)} onAnswer={vi.fn()} />)
    expect(screen.getByTestId('browser-approval')).toBeTruthy()
    expect(screen.getByTestId('bp-card-risk').textContent).toContain(PARAMS.risk_reason)
    expect(screen.getByTestId('bp-card-host').textContent).toBe('assets.example.com')
    expect(screen.getByTestId('bp-card-element').textContent).toBe('Sign in')
    expect(screen.getByTestId('bp-card-info').textContent).toContain('click')
  })

  it('takes the host from a url when no host is sent', () => {
    const info = browserCardInfo('browser_fill', { url: 'https://shop.example.org/cart?x=1' })
    expect(info.host).toBe('shop.example.org')
    expect(info.action).toBe('fill')
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
