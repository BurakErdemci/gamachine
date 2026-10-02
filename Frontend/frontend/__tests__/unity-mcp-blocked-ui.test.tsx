/**
 * `blocked` durumunun KULLANICIYA GÖRÜNEN yüzü — dört sunum yolu ayrı ayrı.
 *
 * Neden dördü ayrı sürülüyor: bu depoda ölçüldü (2026-07-29,
 * [[denetim-kapatma-dersi]] 5. vaka) — bir sınıf kodda dört yolda düzeltilip
 * testte yalnız BİR yol sürülmüştü; kalan üçü sürülünce iki gerçek bulgu
 * çıktı. "Varyantı düzelttim" ile "varyantı ölçtüm" ayrı satırlar.
 *
 * Yollar: (1) anahtarın görünümü, (2) anahtarın başlığı/talimatı,
 * (3) hareket (yalnız "bağlanıyor" görünümü canlı), (4) Ayarlar modalındaki satır.
 *
 * v4 (P1): anahtar maketin `.unity` DOM'unu kullanıyor ve rengi CSS token'ından
 * geliyor; bu yüzden (1) ve (3) artık sınıf adındaki renge değil, CSS'in
 * görünümü seçtiği `data-unity` durumuna bakıyor. Niyet aynı: blocked kapalıdan
 * ve bağlanıyordan AYRIŞIYOR. Dördüncüsü bugün
 * `UNITY_STATUS_CONFIG[blocked]` `undefined` olduğu için TypeError atıyor,
 * yani `blocked` yalnız eksik özellik değil CANLI bir çökme yolu.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { cevir } from '../renderer/lib/i18n'
import type { AIConfig } from '../renderer/components/home/types'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

const BLOCKED_REASON =
  '8080 portu bize ait olmayan bir süreç tarafından kullanılıyor ' +
  '(node · PID 4242). O süreci kapatıp tekrar deneyin.'

const toggleButton = () => screen.getByRole('switch', { name: /unity/i })
// The look CSS draws (maket base.css `.unity[data-unity]`), set on the bay.
const look = () => (screen.getByTestId('unity-bay') as HTMLElement).dataset.unity

afterEach(() => cleanup())

describe('UnityMcpToggle — blocked görünürlüğü', () => {
  it('1) blocked kendi görünümünde (kilit, uyarı halkası) — gri "kapalı" görünümünden ayrışıyor', () => {
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    expect(look()).toBe('blocked')
    expect(screen.getByText(cevir('unity.wordBlocked'))).toBeTruthy()
  })

  it('1b) off ta blocked görünümü YOK — görünüm duruma bağlı, sabit değil', () => {
    render(
      <UnityMcpToggle status="off" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} />
    )
    expect(look()).toBe('off')
    expect(screen.queryByText(cevir('unity.wordBlocked'))).toBeNull()
  })

  it('2) başlık YANLIŞ talimat vermiyor — "Unity açık olmalı" değil, port sorunu', () => {
    // Eski davranış: ternary zincirinin else dalı `home.unityOpen`'a düşüyordu,
    // yani kullanıcıya Unity'yi açmasını söylüyordu. Unity'nin açık olması
    // bu durumu hiç değiştirmiyor; sorun portun sahibi.
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    const title = toggleButton().getAttribute('title') || ''
    expect(title).not.toMatch(/Unity açık olmalı/i)
    expect(title).toMatch(/8080|port/i)
  })

  it('3) blocked hareketsiz — yalnız starting/running "bağlanıyor" (LED koşusu) görünümünde', () => {
    // The chase animation is bound to data-unity="connecting" in shell.css, so the
    // look IS the motion switch: blocked must never land on it.
    const { rerender } = render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    expect(look()).not.toBe('connecting')

    rerender(
      <UnityMcpToggle status="running" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} />
    )
    expect(look()).toBe('connecting')
    rerender(
      <UnityMcpToggle status="starting" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} />
    )
    expect(look()).toBe('connecting')
  })

  it('sebep şeridi görünüyor ve süreç adını taşıyor', () => {
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    const strip = screen.getByRole('alert')
    expect(strip.textContent).toContain('node')
    expect(strip.textContent).toContain('4242')
  })

  it('şerit KALICI — 6 saniyelik silinmeye bağlı değil', () => {
    // Eski `unityMcpError` banner'ı 6 sn sonra siliniyordu; `blocked` kalıcı bir
    // durum olduğu için sebebin de kalıcı olması gerekiyor. Şerit `status`
    // prop'una bağlı, bir zamanlayıcıya değil — zaman ileri alınsa da durur.
    vi.useFakeTimers()
    try {
      render(
        <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                        error={null} onToggle={vi.fn()} />
      )
      vi.advanceTimersByTime(60_000)
      expect(screen.getByRole('alert').textContent).toContain('4242')
    } finally {
      vi.useRealTimers()
    }
  })

  it('connected ta şerit YOK ve anahtar yanık ("on") — iki yön de sınanıyor', () => {
    render(
      <UnityMcpToggle status="connected" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} projectName="Arena" />
    )
    expect(screen.queryByRole('alert')).toBeNull()
    expect(look()).toBe('on')
    expect(toggleButton().getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText('Arena')).toBeTruthy()
  })

  it('unknown "kapalı" görünümünde ve yeşil DEĞİL — bulgu I-2 geri gelmiyor', () => {
    render(
      <UnityMcpToggle status="unknown" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} projectName="Arena" />
    )
    expect(look()).toBe('closed')
    expect(screen.getByText(cevir('unity.wordUnknown'))).toBeTruthy()
    // A stale project name would read as "connected to Arena".
    expect(screen.queryByText('Arena')).toBeNull()
  })

  it('"Neden?" sebebi açıp kapatıyor; blocked gelince sebep kendiliğinden açık', () => {
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    const why = screen.getByRole('button', { name: cevir('unity.why') })
    expect(why.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('unity-bay').classList.contains('why-open')).toBe(true)
    // The popover it controls is the one carrying the reason.
    const pop = document.getElementById(why.getAttribute('aria-controls')!)!
    expect(pop.textContent).toContain('4242')
    fireEvent.click(why)
    expect(why.getAttribute('aria-expanded')).toBe('false')
    expect(screen.getByTestId('unity-bay').classList.contains('why-open')).toBe(false)
    fireEvent.click(why)
    expect(why.getAttribute('aria-expanded')).toBe('true')
  })

  it('blocked DEĞİLKEN elde kalmış sebep gösterilmiyor — ikinci savunma hattı', () => {
    // Hook sebebi durum değişince null'a düşürüyor (birinci hat). Bileşen de
    // kendi başına kontrol ediyor, çünkü tek hatlı bir koruma o hattı kaldıran
    // bir düzenlemede sessizce açılıyor: kullanıcı yabancı sunucuyu kapattıktan
    // sonra bile "port başkasında" okumaya devam ederdi.
    render(
      <UnityMcpToggle status="connected" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={vi.fn()} />
    )
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('blocked ta buton BASILABİLİR — yabancı süreç kapatılınca tekrar denenir', () => {
    const onToggle = vi.fn()
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error={null} onToggle={onToggle} />
    )
    expect((toggleButton() as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(toggleButton())
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('starting te buton kilitli — mevcut koruma korunuyor', () => {
    render(
      <UnityMcpToggle status="starting" toggling={false} reason={null}
                      error={null} onToggle={vi.fn()} />
    )
    expect((toggleButton() as HTMLButtonElement).disabled).toBe(true)
  })

  it('sebep ve geçici hata birlikte gelince İKİSİ de okunabiliyor', () => {
    render(
      <UnityMcpToggle status="blocked" toggling={false} reason={BLOCKED_REASON}
                      error="Unity MCP toggle başarısız." onToggle={vi.fn()} />
    )
    const alerts = screen.getAllByRole('alert')
    expect(alerts).toHaveLength(2)
    const metin = alerts.map(a => a.textContent).join(' | ')
    expect(metin).toContain('4242')
    expect(metin).toContain('başarısız')
  })
})

describe('Settings screen Unity page — blocked row', () => {
  const aiConfig: AIConfig = {
    provider_type: 'subscription',
    api_key: '',
    model_name: 'claude-sonnet-5',
    thinking_level: 'medium',
  }

  const renderModal = (status: any) =>
    render(
      <SettingsScreen
        open
        page="unity"
        aiConfig={aiConfig}
        providersWithKeys={[]}
        onClose={vi.fn()}
        onLogout={vi.fn()}
        onDeleteKey={vi.fn(async () => {})}
        unityMcpStatus={status}
        unityMcpToggling={false}
        onToggleUnityMcp={vi.fn()}
        lang="tr"
        onLangChange={vi.fn()}
      />
    )

  it('blocked ta ÇÖKMÜYOR — UNITY_STATUS_CONFIG[blocked] tanımlı', () => {
    // Bugünkü davranış: `UNITY_STATUS_CONFIG[unityMcpStatus].border` →
    // TypeError, modal hiç açılmıyor. Backend bu değeri `b4065f1`'den beri
    // döndürüyor, yani erişilebilir bir çökme.
    expect(() => renderModal('blocked')).not.toThrow()
    // Round 11: the row is named "Unity connection" (mockup), not "Unity MCP".
    expect(screen.getByText(cevir('set.unity.conn'))).toBeTruthy()
  })

  it('blocked satırı KIRMIZI — "kapalı" görünümünden ayrışıyor', () => {
    // Satırın var olması yetmez: `blocked`'ı `off`un kopyası yapmak çökmeyi
    // önler ama kullanıcıya yine gri "kapalı" gösterir, yani sorunu gizler.
    // v4: the row's colour comes from settings.css keyed on data-tone (the
    // rule is checked below), the same move the shell switch made to data-unity.
    renderModal('blocked')
    const satir = screen.getByTestId('unity-mcp-row')
    expect(satir.textContent).toContain(cevir('set.unity.conn'))
    expect(satir.getAttribute('data-tone')).toBe('danger')
    cleanup()
    renderModal('off')
    expect(screen.getByTestId('unity-mcp-row').getAttribute('data-tone')).not.toBe('danger')
  })

  it('blocked ta AÇIK gibi görünmüyor — anahtar sola bakıyor', () => {
    // `unityMcpStatus !== 'off'` counted blocked as "on" and lit the switch,
    // yet the server is not ours, which is worse than off.
    renderModal('blocked')
    expect(screen.getByTestId('unity-mcp-toggle').getAttribute('data-state')).toBe('off')
    expect(screen.getByTestId('unity-mcp-toggle').getAttribute('aria-checked')).toBe('false')
  })

  it('connected ta anahtar AÇIK — yön korunuyor', () => {
    renderModal('connected')
    expect(screen.getByTestId('unity-mcp-toggle').getAttribute('data-state')).toBe('on')
    expect(screen.getByTestId('unity-mcp-toggle').getAttribute('aria-checked')).toBe('true')
  })

  it('settings.css turns the danger tone and the on state into their look', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/settings.css'), 'utf8')
    // Round 11: the danger tone marks the row's edge and its state word; the switch reads
    // aria-checked, which the component derives from the same "is the server ours" rule as data-state.
    expect(css).toMatch(/\.set-row\[data-tone="danger"\]\s*\{[^}]*var\(--set-warn\)/)
    expect(css).toMatch(/\.set-row\[data-tone="danger"\] \.set-state\s*\{[^}]*var\(--set-warn-text\)/)
    expect(css).toMatch(/\.set-switch\[aria-checked="true"\]\s*\{[^}]*background/)
  })
})
