import { describe, it, expect, afterEach } from 'vitest'
import { aktifDil, aktifDilAyarla, cevir } from '../renderer/lib/i18n'

// vitest.setup.ts announces Turkish for the legacy suite; these tests forget
// that announcement to measure the product default itself.
describe('UI language default', () => {
  afterEach(() => { aktifDilAyarla('tr') })

  it('an empty profile speaks English', () => {
    aktifDilAyarla(null)
    localStorage.removeItem('app-lang')
    expect(aktifDil()).toBe('en')
    expect(cevir('settings.language')).toBe('Language')
  })

  it('a stored Turkish choice wins over the default', () => {
    aktifDilAyarla(null)
    localStorage.setItem('app-lang', 'tr')
    expect(aktifDil()).toBe('tr')
  })
})
