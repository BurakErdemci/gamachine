/**
 * The chat must stay stuck to the bottom through growth that is not a new
 * message: an image that finishes loading, a tool card that expands.
 *
 * Fault measured on Gamachine 4.0.0 (12 Nov 2026): the answer grew, the view
 * stayed where it was and "New messages — jump down" appeared although the
 * user never scrolled. The hook derived "pinned" from the position of EVERY
 * scroll event, including its own smooth follow scroll: when the content grew
 * under that animation (an image arriving), the animation's last event landed
 * hundreds of pixels short of the new bottom and switched following off for
 * good. Growth that is not a message change also never triggered a follow.
 */
import React, { useEffect, useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'

import { useAutoScroll } from '../renderer/hooks/home/useAutoScroll'

const scrollIntoView = vi.fn()
type Entry = { cb: ResizeObserverCallback; targets: Element[] }
const observers: Entry[] = []

class FakeResizeObserver {
  entry: Entry
  constructor(cb: ResizeObserverCallback) {
    this.entry = { cb, targets: [] }
    observers.push(this.entry)
  }
  observe(el: Element) { this.entry.targets.push(el) }
  unobserve(el: Element) { this.entry.targets = this.entry.targets.filter(t => t !== el) }
  disconnect() { this.entry.targets = [] }
}

/** What the browser reports when an image inside the list finishes loading. */
const growLayout = () => act(() => {
  for (const o of observers) if (o.targets.length) o.cb([], o as unknown as ResizeObserver)
})

let origRO: unknown
beforeEach(() => {
  scrollIntoView.mockClear()
  observers.length = 0
  Object.defineProperty(Element.prototype, 'scrollIntoView', { value: scrollIntoView, configurable: true, writable: true })
  origRO = (globalThis as any).ResizeObserver
  ;(globalThis as any).ResizeObserver = FakeResizeObserver
})
afterEach(() => {
  cleanup()
  ;(globalThis as any).ResizeObserver = origRO
})

const scrollEvent = (el: HTMLElement, scrollHeight: number, scrollTop: number, clientHeight = 200) => {
  for (const [name, value] of [['scrollHeight', scrollHeight], ['clientHeight', clientHeight], ['scrollTop', scrollTop]] as const) {
    Object.defineProperty(el, name, { value, configurable: true })
  }
  fireEvent.scroll(el)
}

const JUMP = 'jump down'

const Harness: React.FC<{ itemCount: number }> = ({ itemCount }) => {
  const s = useAutoScroll()
  const [unread, setUnread] = useState(false)
  useEffect(() => { if (!s.followIfPinned()) setUnread(true) }, [itemCount, s.followIfPinned])
  useEffect(() => { if (s.isPinned) setUnread(false) }, [s.isPinned])
  return (
    <div data-testid="scroller" onScroll={s.onScroll}>
      <div data-testid="list">
        {Array.from({ length: itemCount }, (_, i) => <p key={i}>msg {i}</p>)}
        <div ref={s.endRef} />
      </div>
      {unread && <button onClick={() => { s.scrollToBottom(); setUnread(false) }}>{JUMP}</button>}
    </div>
  )
}

describe('auto-follow through late height changes', () => {
  it('follows an image that loads after the message rendered, with no new message', () => {
    render(<Harness itemCount={2} />)
    const el = screen.getByTestId('scroller')
    scrollEvent(el, 1000, 800) // at the bottom
    scrollIntoView.mockClear()
    growLayout() // the image arrives: content grows, messages do not change
    expect(scrollIntoView).toHaveBeenCalled()
    expect(screen.queryByText(JUMP)).toBeNull()
  })

  it('stays pinned when its own follow scroll lands short because content grew under it', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const el = screen.getByTestId('scroller')
    scrollEvent(el, 1000, 800) // at the bottom
    rerender(<Harness itemCount={2} />) // a chunk streams in, the follow scroll starts
    // The smooth scroll moved DOWN, but an image grew the list by 400 px meanwhile:
    // the animation's frames end 400 px above the new bottom.
    scrollEvent(el, 1400, 900)
    scrollEvent(el, 1400, 1000)
    scrollIntoView.mockClear()
    rerender(<Harness itemCount={3} />)
    expect(scrollIntoView).toHaveBeenCalled()
    expect(screen.queryByText(JUMP)).toBeNull()
    scrollIntoView.mockClear()
    growLayout()
    expect(scrollIntoView).toHaveBeenCalled()
  })

  it('scrolling up by the user stops following, also for late growth, and shows the notice', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const el = screen.getByTestId('scroller')
    scrollEvent(el, 1000, 800) // at the bottom
    scrollEvent(el, 1000, 500) // the user scrolls UP
    scrollIntoView.mockClear()
    growLayout()
    rerender(<Harness itemCount={2} />)
    expect(scrollIntoView).not.toHaveBeenCalled()
    expect(screen.getByText(JUMP)).toBeTruthy()
  })

  it('returning to the bottom by hand resumes following of late growth', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const el = screen.getByTestId('scroller')
    scrollEvent(el, 1000, 800)
    scrollEvent(el, 1000, 500) // up
    rerender(<Harness itemCount={2} />)
    expect(screen.getByText(JUMP)).toBeTruthy()
    scrollEvent(el, 1000, 800) // back down by hand
    expect(screen.queryByText(JUMP)).toBeNull()
    scrollIntoView.mockClear()
    growLayout()
    expect(scrollIntoView).toHaveBeenCalled()
  })
})
