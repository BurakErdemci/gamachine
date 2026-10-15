import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import MyApp from '../renderer/pages/_app'
import { AppearanceProvider } from '../renderer/lib/appearance'

const Page = () => <div>page</div>
const zoomCalls = () => (window as any).ipc.invoke.mock.calls.filter(([channel]: any) => channel === 'app-zoom-set')
const renderApp = (pathname: string) => render(<MyApp Component={Page as any} pageProps={{}} router={{ pathname } as any} />)

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('app-text-size', 'large')
  vi.stubGlobal('ipc', { invoke: vi.fn().mockResolvedValue(true), on: vi.fn(() => () => {}) })
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('indicator window zoom', () => {
  it('the /computer-live page never invokes app-zoom-set, even with a non-normal stored text size', async () => {
    const { findByText } = renderApp('/computer-live')
    await findByText('page')
    // Appearance tokens still apply; only the zoom is skipped.
    await waitFor(() => expect(document.documentElement.dataset.theme).toBeTruthy())
    expect(zoomCalls()).toHaveLength(0)
  })

  it('the main app still applies the stored text size', async () => {
    renderApp('/')
    await waitFor(() => expect(zoomCalls().length).toBeGreaterThan(0))
  })

  it('AppearanceProvider applyZoom={false} skips the zoom call', async () => {
    render(<AppearanceProvider applyZoom={false}><div>x</div></AppearanceProvider>)
    await waitFor(() => expect(document.documentElement.dataset.theme).toBeTruthy())
    expect(zoomCalls()).toHaveLength(0)
  })
})
