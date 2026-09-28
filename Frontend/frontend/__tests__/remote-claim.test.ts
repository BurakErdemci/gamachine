import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { claimRemoteMessage, resetRemoteClaimsForTests } from '../renderer/lib/remoteControl'

/**
 * One phone message, several renderer windows: when the claim cannot be
 * written to shared storage, only the primary window may send it.
 * `resetRemoteClaimsForTests()` starts the context of another window.
 */

const SEEN_KEY = 'gamachine.remoteMessages.claimed'

// Exclusive Web Locks with ifAvailable and a wait queue, shared by all contexts.
function fakeLocks() {
  const held = new Map<string, () => void>()
  const waiting = new Map<string, Array<() => void>>()
  const grant = (name: string, cb: (lock: unknown) => any): Promise<any> => {
    let release!: () => void
    const released = new Promise<void>(r => { release = r })
    held.set(name, release)
    const result = Promise.resolve(cb({ name }))
    void Promise.race([result, released]).then(() => {
      held.delete(name)
      waiting.get(name)?.shift()?.()
    })
    return result
  }
  return {
    request(name: string, a: any, b?: any): Promise<any> {
      const opts = typeof a === 'function' ? {} : a
      const cb = typeof a === 'function' ? a : b
      if (!held.has(name)) return grant(name, cb)
      if (opts.ifAvailable) return Promise.resolve(cb(null))
      return new Promise(resolve => {
        const queue = waiting.get(name) ?? []
        queue.push(() => resolve(grant(name, cb)))
        waiting.set(name, queue)
      })
    },
    /** The holder's window closes. */
    drop(name: string) { held.get(name)?.() },
  }
}

let setItem: ReturnType<typeof vi.spyOn> | null = null
const failStorage = () => {
  setItem = vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('quota') })
}
const setLocks = (value: unknown) => Object.defineProperty(navigator, 'locks', { configurable: true, value })

beforeEach(() => {
  resetRemoteClaimsForTests(true)
  localStorage.clear()
})

afterEach(() => {
  setItem?.mockRestore()
  setItem = null
  delete (navigator as any).locks
  resetRemoteClaimsForTests(true)
})

describe('remote message claim without durable storage', () => {
  it('with Web Locks: only the primary lock holder claims; the next window takes over when it closes', async () => {
    const locks = fakeLocks()
    setLocks(locks)
    failStorage()
    expect(await claimRemoteMessage('r1')).toBe(true) // window A becomes primary
    resetRemoteClaimsForTests() // window B
    expect(await claimRemoteMessage('r1')).toBe(false)
    expect(await claimRemoteMessage('r2')).toBe(false)
    expect(localStorage.getItem(SEEN_KEY)).toBeNull()
    locks.drop('gamachine-remote-primary') // window A closes
    await vi.waitFor(async () => expect(await claimRemoteMessage(`r3-${Math.random()}`)).toBe(true))
  })

  it('a failing lock request counts as unrecorded: the claim goes to the primary only', async () => {
    const locks = fakeLocks()
    setLocks({
      request: (name: string, a: any, b?: any) => name === 'gamachine-remote-message-claim'
        ? Promise.reject(new Error('lock failed')) : locks.request(name, a, b),
    })
    expect(await claimRemoteMessage('r1')).toBe(true)
    resetRemoteClaimsForTests()
    expect(await claimRemoteMessage('r2'), 'storage worked but was written without the lock').toBe(false)
    expect(await claimRemoteMessage('r1')).toBe(false)
  })

  it('without Web Locks: an existing primary keeps it, and concurrent windows elect exactly one', async () => {
    failStorage()
    expect(await claimRemoteMessage('r1')).toBe(true)
    resetRemoteClaimsForTests()
    expect(await claimRemoteMessage('r1')).toBe(false)
    resetRemoteClaimsForTests(true)
    const first = claimRemoteMessage('r2')
    resetRemoteClaimsForTests()
    const second = claimRemoteMessage('r2')
    resetRemoteClaimsForTests()
    const third = claimRemoteMessage('r2')
    const results = await Promise.all([first, second, third])
    expect(results.filter(Boolean)).toHaveLength(1)
  })

  it('a recorded claim still works in any window, primary or not', async () => {
    setLocks(fakeLocks())
    expect(await claimRemoteMessage('r1')).toBe(true)
    resetRemoteClaimsForTests()
    expect(await claimRemoteMessage('r1')).toBe(false)
    expect(await claimRemoteMessage('r2'), 'window B is not primary but storage works').toBe(true)
  })
})
