/**
 * The model menu stays 8px inside the window at every width: right-aligned under the chip when
 * it fits, shifted right (smaller `right` offset) when the chip sits too far from the edge.
 * jsdom has no layout, so the chip rect, the menu width and innerWidth are mocked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, cleanup, act } from '@testing-library/react'

import { ModelSelector } from '../renderer/components/home/ModelSelector'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const MENU_W = 600
let wrapRight = 1000

const props = () => ({
  aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5-5', api_key: '' },
  setAiConfig: vi.fn(),
  availableModels: { local: [], subscription: [], cloud: [] },
  providersWithKeys: [],
  effectiveProvider: 'subscription',
  displayModelName: 'Claude Opus 5.5 (CLI)',
  isModelDropdownOpen: true,
  setIsModelDropdownOpen: vi.fn(),
  modelOrToggles: {},
  setModelOrToggles: vi.fn(),
  user: { id: 1, name: 'B', sessionToken: 't' },
  fetchAvailableModels: vi.fn(),
  setShowSettings: vi.fn(),
  API: 'http://x',
  axios: {
    get: vi.fn(async () => ({ data: { models: [] } })),
    post: vi.fn(async () => ({ data: {} })),
  },
  showToast: vi.fn(),
  conversationId: 7,
  usage: null,
}) as any

const setWidth = (w: number) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: w })
}
const menuOf = (c: HTMLElement) => c.querySelector('[data-testid="model-menu"]') as HTMLElement
// The menu's left edge in viewport coordinates, derived from the inline right offset.
const leftEdge = (c: HTMLElement) => wrapRight - parseFloat(menuOf(c).style.right || '0') - MENU_W

let origRect: typeof Element.prototype.getBoundingClientRect
let origWidth: PropertyDescriptor | undefined
const origInner = window.innerWidth

beforeEach(() => {
  aktifDilAyarla('tr')
  origRect = Element.prototype.getBoundingClientRect
  origWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const right = this.classList.contains('model-wrap') ? wrapRight : 0
    return { x: 0, y: 0, top: 0, left: right, right, bottom: 0, width: 0, height: 0, toJSON: () => ({}) } as DOMRect
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get(this: HTMLElement) { return this.classList.contains('model-menu') ? MENU_W : 0 },
  })
})

afterEach(() => {
  cleanup()
  Element.prototype.getBoundingClientRect = origRect
  if (origWidth) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', origWidth)
  else delete (HTMLElement.prototype as any).offsetWidth
  setWidth(origInner)
})

describe('model menu window clamp', () => {
  it('wide window: stays right-aligned under the chip (right offset 0)', () => {
    setWidth(1400)
    wrapRight = 1000
    const { container } = render(<ModelSelector {...props()} />)
    expect(parseFloat(menuOf(container).style.right)).toBe(0)
  })

  it('680px window, chip right edge at 560: menu left edge is 8 and right edge is inside the margin', () => {
    setWidth(680)
    wrapRight = 560
    const { container } = render(<ModelSelector {...props()} />)
    expect(leftEdge(container)).toBe(8)
    expect(leftEdge(container) + MENU_W).toBeLessThanOrEqual(680 - 8)
  })

  it('resize while open re-clamps', () => {
    setWidth(1400)
    wrapRight = 1000
    const { container } = render(<ModelSelector {...props()} />)
    expect(parseFloat(menuOf(container).style.right)).toBe(0)
    act(() => { setWidth(680); wrapRight = 560; window.dispatchEvent(new Event('resize')) })
    expect(leftEdge(container)).toBe(8)
  })

  it('removes the resize listener on close', () => {
    setWidth(1400)
    wrapRight = 1000
    const add = vi.spyOn(window, 'addEventListener')
    const remove = vi.spyOn(window, 'removeEventListener')
    const p = props()
    const { rerender } = render(<ModelSelector {...p} />)
    const added = add.mock.calls.filter(c => c[0] === 'resize').map(c => c[1])
    expect(added.length).toBe(1)
    rerender(<ModelSelector {...p} isModelDropdownOpen={false} />)
    const removed = remove.mock.calls.filter(c => c[0] === 'resize').map(c => c[1])
    expect(removed).toContain(added[0])
    add.mockRestore()
    remove.mockRestore()
  })
})
