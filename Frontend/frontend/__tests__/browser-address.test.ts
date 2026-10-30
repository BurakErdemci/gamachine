import { describe, expect, it } from 'vitest'
import { addressToUrl, displayAddress } from '../renderer/lib/browserAddress'

describe('addressToUrl', () => {
  it('opens web addresses and searches everything else', () => {
    expect(addressToUrl('https://github.com/x')).toBe('https://github.com/x')
    expect(addressToUrl('github.com')).toBe('https://github.com')
    expect(addressToUrl('docs.unity3d.com/Manual/index.html')).toBe('https://docs.unity3d.com/Manual/index.html')
    expect(addressToUrl('  github  ')).toBe('https://www.google.com/search?q=github')
    expect(addressToUrl('unity 6 sürüm notları')).toBe('https://www.google.com/search?q=unity%206%20s%C3%BCr%C3%BCm%20notlar%C4%B1')
    expect(addressToUrl('node.js nedir')).toBe('https://www.google.com/search?q=node.js%20nedir')
    expect(addressToUrl('3.14')).toBe('https://www.google.com/search?q=3.14')
    expect(addressToUrl('   ')).toBeNull()
  })
})

describe('displayAddress', () => {
  it('shows only the site name for web pages', () => {
    expect(displayAddress('https://www.github.com/signup?source=form-home')).toBe('github.com')
    expect(displayAddress('https://docs.unity3d.com/Manual')).toBe('docs.unity3d.com')
    expect(displayAddress('about:blank')).toBe('about:blank')
    expect(displayAddress('')).toBe('')
  })
})
