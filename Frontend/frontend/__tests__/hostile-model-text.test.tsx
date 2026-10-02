/**
 * MODELİN ve UZAK KATALOĞUN yazdığı metin, gösterildiği her yerde temizleniyor.
 *
 * Denetim iki ayrı yüzeyde aynı sınıfı buldu (30 Ağu 2026):
 *
 *   1. Komut onay kartı `command`ı olduğu gibi çiziyordu.
 *   2. Model seçici, sağlayıcının canlı kataloğundan gelen `name`i olduğu gibi
 *      çiziyordu — hem satırda hem seçili model etiketinde.
 *
 * U+202E React'in kaçırdığı türden bir tehdit değil: markup DEĞİL, çizim yönü.
 * React onu kaçırmıyor çünkü kaçırılacak bir şey yok — tarayıcı karakteri
 * onurlandırıp satırın kalanını ters çiziyor. Sonucu onay kartında ağır:
 * `printf safe<U+202E>; rm -rf /` ekranda zararsız görünüp zararlı olanı
 * onaylatabiliyor. Gösterdiğinden BAŞKASINI onaylayan bir kapı, kapı değildir.
 *
 * Testlerin sabitlediği ikinci şey, bu depoda adı konmuş arıza sınıfı:
 * "kapı bir yolda var, öbür yolda yok". `stripBidi` zaten vardı ve yalnız soru
 * kartı ile bildirimlerde kullanılıyordu; var olması korunuyor sanılmasına
 * yetti. O yüzden aşağıda TEK BİR yüzey değil, aynı metnin çizildiği bütün
 * yollar tek tek sürülüyor.
 *
 * Ve temizlik YALNIZ gösterimde: `onConfirm` gerçek komutu çalıştırıyor,
 * `m.id` ham kaydediliyor. Kullanıcı gerçek dizgeyi onaylamalı — dürüst
 * gösterilmiş hâliyle.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { CommandApproval } from '../renderer/components/home/CommandApproval'
import { ModelSelector } from '../renderer/components/home/ModelSelector'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { stripBidi } from '../renderer/lib/modelText'

afterEach(() => cleanup())

const RLO = '\u202E'   // RIGHT-TO-LEFT OVERRIDE
const PDF = '\u202C'   // POP DIRECTIONAL FORMATTING

/** Ekranda hiçbir yönlendirme denetimi kalmadı mı. */
const govdedeKontrolYok = () =>
  expect(document.body.textContent || '').not.toMatch(/[\u202A-\u202E\u2066-\u2069\u200E\u200F]/)

describe('komut onay kartı', () => {
  const HASIM = `printf safe${RLO}; rm -rf /`

  it('gösterilen komutta yönlendirme denetimi KALMIYOR', () => {
    render(<CommandApproval command={HASIM} onConfirm={() => {}} onCancel={() => {}} />)
    const kod = document.querySelector('code')
    expect(kod).toBeTruthy()
    expect(kod!.textContent || '').not.toContain(RLO)
  })

  it('komutun geri kalanı OLDUĞU GİBİ duruyor — sansür değil, temizlik', () => {
    // Tehlikeli kısmı gizlemek de bir yalan olurdu: kullanıcı `rm -rf /`
    // parçasını görmeli, yalnız doğru sırada.
    render(<CommandApproval command={HASIM} onConfirm={() => {}} onCancel={() => {}} />)
    const metin = document.querySelector('code')!.textContent || ''
    expect(metin).toContain('printf safe')
    expect(metin).toContain('; rm -rf /')
  })

  it('onay GERÇEK komutu onaylıyor — temizlenmiş kopyayı değil', () => {
    // Kart yalnız gösterimi temizliyor. `onConfirm` argümansız: çalıştırılacak
    // komut çağıranda duruyor ve kart ona dokunmuyor. Bu test o sözleşmeyi
    // sabitliyor — bir gün kart temizlenmiş metni geri gönderirse, kullanıcı
    // onayladığından BAŞKA bir komut çalışır.
    const onay = vi.fn()
    render(<CommandApproval command={HASIM} onConfirm={onay} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('Komutu Çalıştır'))
    expect(onay).toHaveBeenCalledTimes(1)
    // Kart geri hiçbir komut metni YOLLAMIYOR (tek argümanı React'in tıklama
    // olayı). Temizlenmiş bir kopyayı geri yollasaydı, çalışan komut
    // kullanıcının onayladığı komuttan farklı olurdu — ve fark tam olarak
    // saldırganın koyduğu karakterler kadar.
    expect(onay.mock.calls[0].some((a: unknown) => typeof a === 'string')).toBe(false)
  })

  it('Unity kartı da aynı kapıdan geçiyor', () => {
    // İki `kind` var ve ikisi de aynı `command` alanını çiziyor; birini
    // temizleyip diğerini bırakmak bu bulgunun ta kendisi olurdu.
    render(<CommandApproval kind="unity" command={`Assets/Safe${RLO}sc.txt`} onConfirm={() => {}} onCancel={() => {}} />)
    expect(document.querySelector('code')!.textContent || '').not.toContain(RLO)
  })
})

const secici = {
  setAiConfig: () => {},
  providersWithKeys: ['anthropic'],
  effectiveProvider: 'anthropic',
  isModelDropdownOpen: true,
  setIsModelDropdownOpen: () => {},
  modelOrToggles: {},
  setModelOrToggles: () => {},
  user: null,
  fetchAvailableModels: () => {},
  setShowSettings: () => {},
  API: '',
  axios: { get: async () => ({ data: {} }), post: async () => ({ data: {} }) },
  showToast: () => {},
}

describe('canlı katalogdan gelen model adı', () => {
  const AD = `safe${RLO}-model`

  it('bulut satırında yönlendirme denetimi KALMIYOR', () => {
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'anthropic', model_name: 'remote-id' } as any}
        displayModelName="secili"
        availableModels={{
          subscription: [], local: [],
          cloud: [{ id: 'remote-id', name: AD, provider: 'anthropic' }],
          cloud_sources: { anthropic: 'live' },
        }}
      />,
    )
    // Satır gerçekten çizildi mi — yokluk ancak varlık kanıtlanınca bir şey söyler.
    expect(screen.getAllByText('safe-model').length).toBeGreaterThan(0)
    govdedeKontrolYok()
  })

  it('SEÇİLİ MODEL etiketi de temizleniyor', () => {
    // Denetimin adıyla çağırdığı ikinci yol. Satır temizlenip bu bırakılınca
    // yüzey korunmuş GÖRÜNÜR ama korunmaz.
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'anthropic', model_name: 'remote-id' } as any}
        displayModelName={AD}
        isModelDropdownOpen={false}
        availableModels={{ subscription: [], local: [], cloud: [] }}
      />,
    )
    expect(screen.getByText('safe-model')).toBeTruthy()
    govdedeKontrolYok()
  })

  it('abonelik (CLI) satırı da temizleniyor', () => {
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'subscription', model_name: 'claude-x' } as any}
        displayModelName="secili"
        availableModels={{
          subscription: [{ id: 'claude-x', name: `Claude${RLO}-9`, provider: 'anthropic' }],
          local: [], cloud: [],
        }}
      />,
    )
    expect(screen.getByText('Claude-9')).toBeTruthy()
    govdedeKontrolYok()
  })

  it('yerel (Ollama) satırı da temizleniyor', () => {
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'ollama', model_name: 'qwen' } as any}
        displayModelName="secili"
        availableModels={{
          subscription: [], cloud: [],
          local: [{ id: 'qwen', name: `Qwen${RLO}3-8B`, provider: 'ollama' }],
        }}
      />,
    )
    expect(screen.getByText('Qwen3-8B')).toBeTruthy()
    govdedeKontrolYok()
  })

  it('arama sonuçları da temizleniyor — ayrı bir çizim yolu', () => {
    // Arama, satırları AYRI bir dalda çiziyor. Grup dalını temizleyip bunu
    // bırakmak, kapının bir yolda açık kalması demek olurdu.
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'anthropic', model_name: 'remote-id' } as any}
        displayModelName="secili"
        availableModels={{
          subscription: [], local: [],
          cloud: [{ id: 'remote-id', name: AD, provider: 'anthropic' }],
        }}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText('Model ara…'), { target: { value: 'safe' } })
    expect(screen.getAllByText('safe-model').length).toBeGreaterThan(0)
    govdedeKontrolYok()
  })

  it('sağlayıcı adı da uzak katalogdan geliyor ve temizleniyor', () => {
    render(
      <ModelSelector
        {...(secici as any)}
        aiConfig={{ provider_type: 'anthropic', model_name: 'remote-id' } as any}
        displayModelName="secili"
        availableModels={{
          subscription: [], local: [],
          cloud: [{ id: 'remote-id', name: 'Duz Ad', provider: `evil${PDF}corp` }],
        }}
      />,
    )
    govdedeKontrolYok()
  })
})

describe('ayarlar ekranındaki model önerileri', () => {
  it('öneri çipi de temizleniyor — aynı katalog, ikinci ekran', () => {
    render(
      <SettingsScreen
        {...({
          // Round 11: the live list is the "Varsayılan model" list on the Modeller page.
          open: true, page: 'modeller', providersWithKeys: ['anthropic'], onChange: () => {}, onClose: () => {},
          onSave: async () => {}, onLogout: () => {}, onDeleteKey: async () => {},
          unityMcpStatus: 'off', unityMcpToggling: false, onToggleUnityMcp: () => {},
          lang: 'tr', onLangChange: () => {},
        } as any)}
        aiConfig={{ provider_type: 'anthropic', model_name: '', api_key: '' } as any}
        availableModels={{
          local: [], subscription: [],
          cloud: [{ id: 'claude-x', name: `Claude${RLO}Opus`, provider: 'anthropic' }],
        }}
      />,
    )
    expect(screen.getByText('ClaudeOpus')).toBeTruthy()
    govdedeKontrolYok()
  })
})

describe('sanitizer sözleşmesi', () => {
  it('bütün yönlendirme denetimleri siliniyor, başka hiçbir şey silinmiyor', () => {
    // Kapının kendisi de sürülüyor: bileşen testleri onu çağırmayı unutursa
    // kırmızı olur, ama yanlış çalışırsa hepsi birden sessizce yeşil kalırdı.
    expect(stripBidi('a\u202Ab\u202Bc\u202Cd\u202De\u202Ef\u2066g\u2069h\u200Ei\u200Fj'))
      .toBe('abcdefghij')
    expect(stripBidi('düz metin — çizgi ve ünlü korunur')).toBe('düz metin — çizgi ve ünlü korunur')
  })
})
