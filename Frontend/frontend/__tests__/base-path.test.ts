import { afterEach, describe, expect, it, vi } from 'vitest'
import { withBase } from '../renderer/lib/basePath'

describe('withBase', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('returns the path unchanged when no base path is set', () => {
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '')
    expect(withBase('/models/mannequin.glb')).toBe('/models/mannequin.glb')
  })

  it('prefixes the path with the base path when set', () => {
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/app')
    expect(withBase('/monaco/vs')).toBe('/app/monaco/vs')
  })
})
