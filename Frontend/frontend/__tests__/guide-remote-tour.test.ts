import { describe, expect, it, vi } from 'vitest'

const remote = vi.hoisted(() => ({ page: false }))
vi.mock('../renderer/lib/remote/transport', () => ({ isRemotePage: () => remote.page }))

import { tourDone } from '../renderer/lib/guide/storage'

const empty = { getItem: () => null, setItem: () => {} }

describe('core tour on the web remote', () => {
  it('counts as done on the relay page, whose browser storage never saw the tour', () => {
    remote.page = true
    expect(tourDone(empty)).toBe(true)
  })

  it('still follows the stored mark on the desktop', () => {
    remote.page = false
    expect(tourDone(empty)).toBe(false)
    expect(tourDone({ getItem: () => '1', setItem: () => {} })).toBe(true)
  })
})
