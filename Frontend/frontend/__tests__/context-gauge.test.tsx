/**
 * Kalıcı bağlam/kullanım göstergesi — GÖRÜNÜRLÜK ve DÜRÜSTLÜK.
 *
 * İki arıza kaydı bu testleri doğurdu (ölçüldü 30 Ağu 2026):
 *
 * 1. Gösterge `contextUsage.percent > 0` koşuluyla çiziliyordu, yani ilk tur
 *    bitene kadar ekranda yoktu. İstenen şey "sürekli görünen kalıcı bir yer"
 *    olduğu için bu koşul isteğin tam tersiydi.
 * 2. Yüzde bir ÖLÇÜM değil: yalnız veritabanına yazılan yazışma metnini
 *    sayıyor; araç çıktıları, sistem talimatı ve CLI oturumlarının kendi
 *    diskteki geçmişi sayılmıyor. İşaretsiz bir "%12" onu ölçüm gibi gösterir.
 *
 * Ve gerçek token 8 çalıştırma yolunun yalnız 4'ünde var. Kalan 4'te (Codex,
 * agy, cursor/copilot/opencode/kimi, basit yol) sıfır göstermek "hiç
 * harcamadın" demek olurdu — bilgi yokluğu ile sıfır ölçüm aynı şey değil.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

import { ControlPanel } from '../renderer/components/home/ControlPanel'

afterEach(() => cleanup())

const temelProps = {
  thinkingLevel: 'medium' as any,
  setThinkingLevel: () => {},
  isAnalyzingProject: false,
  analyzeProject: async () => {},
  exportMemory: async () => {},
  importMemory: async () => {},
  compactConversation: async () => {},
  isCompacting: false,
}

const ciz = (ekler: Record<string, any>) =>
  render(<ControlPanel {...(temelProps as any)} activeConvId={7} {...ekler} />)

describe('görünürlük', () => {
  it('aktif sohbet varken ilk tur BİTMEDEN de görünüyor', () => {
    ciz({ contextUsage: undefined })
    expect(screen.getByTestId('context-gauge')).toBeTruthy()
  })

  it('sohbet yokken hiç çizilmiyor', () => {
    render(<ControlPanel {...(temelProps as any)} activeConvId={null} />)
    expect(screen.queryByTestId('context-gauge')).toBeNull()
  })

  it('verisi yokken boş halka değil "veri yok" yazıyor', () => {
    // Boş bir halka "doluluk sıfır" diye okunur; ikisi aynı şey değil.
    ciz({ contextUsage: undefined })
    expect(screen.getByTestId('context-percent').textContent).toBe('henüz veri yok')
  })
})

describe('yüzdenin dürüstlüğü', () => {
  it('yüzde tahmin işaretiyle gösteriliyor', () => {
    ciz({ contextUsage: { percent: 12, should_compact: false, message_count: 4, estimated: true } })
    expect(screen.getByTestId('context-percent').textContent).toBe('~%12')
  })

  it('GERÇEK veri gelince tahmin işareti kalkıyor ve asıl sayı yazılıyor', () => {
    // `/context` raporu geldiğinde yüzde artık tahmin değil. "~" işaretini
    // ölçülmüş bir sayının üstünde bırakmak da ters yönde bir yalan olurdu.
    ciz({
      contextUsage: {
        percent: 7, should_compact: false, message_count: 12, estimated: false,
        real: { used: '69.9k', total: '1m', model: 'claude-opus-5' },
      },
    })
    const metin = screen.getByTestId('context-percent').textContent || ''
    expect(metin).toContain('%7')
    expect(metin).toContain('69.9k/1m')
    expect(metin).not.toContain('~')
  })

  it('başlık, neyin sayılmadığını açıkça yazıyor', () => {
    ciz({ contextUsage: { percent: 12, should_compact: false, message_count: 4, estimated: true } })
    const baslik = screen.getByTestId('context-gauge').getAttribute('title') || ''
    expect(baslik).toContain('tahmin')
    expect(baslik).toContain('araç çıktıları')
  })

  it('GERÇEK veri geldiğinde başlık artık tahmin metni DEĞİL', () => {
    // Denetim bulgusu (30 Ağu 2026): sayı ölçümdü (`%7 · 69.9k/1m`,
    // `estimated: false`) ama başlık koşulsuz `usage.estimateTitle` idi, yani
    // "Yaklaşık doluluk… bu bir tahmin" diyordu. Sayı ile güven etiketi
    // birbirini yalanlayınca kullanıcı hangisine inanacağını seçemiyor —
    // ölçülmüş bir sayıyı tahmin diye etiketlemek, tahmini ölçüm diye
    // etiketlemek kadar yanlış, yalnız ters yönde.
    ciz({
      contextUsage: {
        percent: 7, should_compact: false, message_count: 12, estimated: false,
        real: { used: '69.9k', total: '1m', model: 'claude-opus-5' },
      },
    })
    const baslik = screen.getByTestId('context-gauge').getAttribute('title') || ''
    expect(baslik).not.toMatch(/tahmin|yaklaşık/i)
    // Ve boş kalmıyor: gerçek sayı başlıkta da duruyor.
    expect(baslik).toContain('69.9k')
    expect(baslik).toContain('1m')
  })

  it('veri hiç yokken (null) gösterge "veri yok" diyor, %0 demiyor', () => {
    // `null` = ÖLÇÜM YOK. Sıfır bir tahminle aynı dala düşerse, başarısız bir
    // istek ölçülmüş bir "neredeyse boş" gibi okunur.
    ciz({ contextUsage: null })
    expect(screen.getByTestId('context-percent').textContent).toBe('henüz veri yok')
    expect(screen.getByTestId('context-gauge').getAttribute('title')).toBe('henüz veri yok')
  })
})

describe('the removed per-turn token readout', () => {
  // Removed 5 Sep 2026. The counter only summed SSE `turn_usage` events; 4 of
  // the 8 run paths report no tokens, and the counter reset on every app
  // restart and conversation switch. The user found the numbers wildly wrong
  // and asked for the readout to go. These tests stay so it cannot come back
  // unnoticed.
  it('no longer draws a token/cost row', () => {
    ciz({
      contextUsage: { percent: 3, should_compact: false, message_count: 2 },
      sessionUsage: { input_tokens: 41_200, output_tokens: 800, cost_usd: 0.21, turns: 2 },
    } as any)
    expect(screen.queryByTestId('session-tokens')).toBeNull()
    expect(screen.queryByTestId('session-tokens-none')).toBeNull()
    const metin = screen.getByTestId('context-gauge').parentElement?.textContent || ''
    expect(metin).not.toContain('tok')
    expect(metin).not.toContain('$')
  })

  it('keeps the memory ring and the "Kullanim" button', () => {
    ciz({
      contextUsage: { percent: 3, should_compact: false, message_count: 2 },
      onToggleReports: () => {},
    })
    expect(screen.getByTestId('context-gauge')).toBeTruthy()
    expect(screen.getByTestId('reports-toggle')).toBeTruthy()
  })
})

describe('uyarı eşiği', () => {
  // v4 strip: the threshold marker is a quiet dot with a state attribute, not a pinging
  // Tailwind animation; the tests read the attribute (the old one read `.animate-ping`).
  it('sıkıştırma eşiği aşılınca nabız işareti çıkıyor', () => {
    const { container } = ciz({
      contextUsage: { percent: 91, should_compact: true, message_count: 40 },
    })
    expect(container.querySelector('[data-should-compact]')).toBeTruthy()
  })

  it('eşik altında nabız işareti yok', () => {
    const { container } = ciz({
      contextUsage: { percent: 40, should_compact: false, message_count: 10 },
    })
    expect(container.querySelector('[data-should-compact]')).toBeNull()
  })
})
