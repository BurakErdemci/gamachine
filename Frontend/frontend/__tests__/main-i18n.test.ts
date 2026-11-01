import { beforeEach, describe, expect, it, vi } from 'vitest'

const locale = vi.hoisted(() => vi.fn())
vi.mock('electron', () => ({ app: { getLocale: locale } }))

beforeEach(() => {
  vi.resetModules()
  locale.mockReset()
})

describe('main process language', () => {
  it('keeps Turkish when Electron has no usable locale', async () => {
    const { uiLanguage, mt } = await import('../main/helpers/main-i18n')
    expect(uiLanguage()).toBe('tr')
    expect(mt('noteSave')).toBe('Kaydet')
    locale.mockImplementation(() => { throw new Error('app unavailable') })
    expect(uiLanguage()).toBe('tr')
  })

  it.each([['tr-TR', 'tr'], ['TR', 'tr'], ['en-US', 'en'], ['de-DE', 'en']])(
    'derives %s as %s before an announcement', async (value, expected) => {
      locale.mockReturnValue(value)
      const { uiLanguage } = await import('../main/helpers/main-i18n')
      expect(uiLanguage()).toBe(expected)
    },
  )

  it('accepts only exact language values and preserves the announced choice', async () => {
    locale.mockReturnValue('en-US')
    const { setUiLanguage, uiLanguage } = await import('../main/helpers/main-i18n')
    for (const junk of [null, undefined, 1, {}, ['tr'], 'TR', 'en-US', '']) {
      setUiLanguage(junk)
      expect(uiLanguage()).toBe('en')
    }
    setUiLanguage('tr')
    setUiLanguage('junk')
    expect(uiLanguage()).toBe('tr')
    setUiLanguage('en')
    locale.mockReturnValue('tr-TR')
    expect(uiLanguage()).toBe('en')
  })

  it('has matching nonempty tables and matching interpolation variables', async () => {
    const { TR, EN } = await import('../main/helpers/main-i18n')
    expect(Object.keys(EN).sort()).toEqual(Object.keys(TR).sort())
    for (const key of Object.keys(TR) as (keyof typeof TR)[]) {
      expect(EN[key].trim()).not.toBe('')
      expect([...new Set(EN[key].match(/\{\w+\}/g))].sort()).toEqual([...new Set(TR[key].match(/\{\w+\}/g))].sort())
    }
  })

  it('interpolates values once without interpreting their contents', async () => {
    const { setUiLanguage, mt } = await import('../main/helpers/main-i18n')
    setUiLanguage('en')
    expect(mt('updateReadyMessage', { version: '5.0' })).toBe('A new version is ready: v5.0')
    expect(mt('trashMessage', { name: '{path} $&' })).toContain('"{path} $&"')
  })

  it('translates the note-box errors and approval-mode errors', async () => {
    const { setUiLanguage, mt } = await import('../main/helpers/main-i18n')
    expect(mt('noteLimit', { max: 50 })).toBe('En fazla 50 not')
    setUiLanguage('en')
    expect(mt('noteLimit', { max: 50 })).toBe('At most 50 notes')
    expect(mt('noteSaveFailed')).toBe('The note could not be saved, try again')
    expect(mt('modeInvalid')).toBe('Invalid approval mode.')
    expect(mt('modeSendFailed')).toBe('The approval mode could not be sent to the backend.')
  })

  it('builds the next note box and annotate overlay in the announced language', async () => {
    const { setUiLanguage } = await import('../main/helpers/main-i18n')
    const { noteBoxHtml, overlayHtml } = await import('../main/helpers/browser-host')
    expect(noteBoxHtml()).toContain('placeholder="Yorum ekle…"')
    setUiLanguage('en')
    expect(noteBoxHtml()).toContain('placeholder="Add a comment…"')
    expect(noteBoxHtml()).toContain('aria-label="Add a comment"')
    expect(noteBoxHtml()).toContain('</span>Save</button>')
    expect(overlayHtml()).toContain('Annotate · click an element or draw an area · Esc to exit')
    setUiLanguage('tr')
    expect(overlayHtml()).toContain('İşaretleme ·')
  })

  it('escapes all translated HTML text and attribute characters', async () => {
    const i18n = await import('../main/helpers/main-i18n')
    const { noteBoxHtml, overlayHtml } = await import('../main/helpers/browser-host')
    const spy = vi.spyOn(i18n, 'mt').mockReturnValue('<tag a="x">&\'</tag>')
    try {
      for (const html of [noteBoxHtml(), overlayHtml()]) {
        expect(html).toContain('&lt;tag a=&quot;x&quot;&gt;&amp;&#39;&lt;/tag&gt;')
        expect(html).not.toContain('<tag')
      }
    } finally { spy.mockRestore() }
  })

  it.each([['win32', 'Recycle Bin'], ['darwin', 'Trash']])('uses the English %s trash prompt', async (platform, bin) => {
    const { setUiLanguage } = await import('../main/helpers/main-i18n')
    const { trashPrompt } = await import('../main/helpers/project-trash')
    setUiLanguage('en')
    const prompt = trashPrompt('Arena', platform)
    expect(prompt).toEqual({
      message: 'The project folder "Arena" will be moved',
      detail: `Arena\n\nIt will be moved to the ${bin}. You can restore it from there.`,
      go: `Move to ${bin}`,
      cancel: 'Cancel',
    })
  })
})
