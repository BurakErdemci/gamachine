/**
 * Ayarlar ekranındaki model önerileri CANLI listeden gelmeli.
 *
 * 30 Ağu 2026: elle yazılı bulut kataloğu backend'den silindi, ama bu dosyada
 * İKİNCİ bir kopya kalmıştı ve Burak onu sahada gördü — Groq için
 * `llama-3.3-70b-versatile` öneriyordu, oysa o model 16 Ağu'da kapatılmıştı.
 *
 * Kalıbın adı bu depoda kayıtlı: kapı bir dala konup diğeri açık bırakılıyor.
 * Ve bu hâli daha sinsi, çünkü ortada "düzeltilmiş" bir yer var — bakan kişi
 * sorunun kapandığını sanıyor.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'

afterEach(() => cleanup())

const temel = {
  open: true,
  // Round 11: the live list is the "Varsayılan model" list on the Modeller page (the modal's
  // eight suggestion chips for one provider became one select over every provider).
  page: 'modeller',
  providersWithKeys: ['anthropic'],
  onClose: () => {},
  onLogout: () => {},
  onDeleteKey: async () => {},
  unityMcpStatus: 'off' as any,
  unityMcpToggling: false,
  onToggleUnityMcp: () => {},
  lang: 'tr' as any,
  onLangChange: () => {},
}

const ciz = (provider: string, cloud: any[], ekler: any = {}) =>
  render(
    <SettingsScreen
      {...(temel as any)}
      aiConfig={{ provider_type: provider, model_name: '', api_key: '' } as any}
      availableModels={{ local: [], subscription: [], cloud, ...ekler }}
    />,
  )

const M = (id: string, name: string, provider: string) => ({ id, name, provider })
const secenekler = () => Array.from(screen.getByTestId('default-model-select').querySelectorAll('option')).map(o => o.textContent)
const grup = (label: string) => screen.getByTestId('default-model-select').querySelector(`optgroup[label="${label}"]`)

describe('varsayılan model listesi', () => {
  it('seçili sağlayıcının CANLI modellerini gösteriyor', () => {
    ciz('anthropic', [M('claude-opus-9', 'Claude Opus 9', 'anthropic')])
    expect(screen.getByText('Claude Opus 9')).toBeTruthy()
  })

  it('her sağlayıcının modeli kendi adının altında — karışmıyor', () => {
    ciz('anthropic', [
      M('claude-opus-9', 'Claude Opus 9', 'anthropic'),
      M('gpt-9', 'GPT-9', 'openai'),
    ])
    expect(grup('Anthropic')!.textContent).toContain('Claude Opus 9')
    expect(grup('Anthropic')!.textContent).not.toContain('GPT-9')
    expect(grup('OpenAI')!.textContent).toContain('GPT-9')
  })

  it('artık ELLE YAZILI bir model önerisi kalmadı', () => {
    // Liste boşsa hiç öneri çıkmamalı. Eski kod burada sabit bir katalog
    // gösteriyordu ve o katalog ölü bir modeli öneriyordu.
    const { container } = ciz('groq', [])
    expect(container.textContent).not.toContain('llama-3.3-70b-versatile')
    expect(container.textContent).not.toContain('Llama 3.3 70B')
  })

  it('liste boşken seçenek uydurulmuyor', () => {
    ciz('groq', [])
    // Yalnız "seçili olan" yer tutucusu kalıyor; katalogdan tek satır yok.
    expect(secenekler()).toHaveLength(1)
    expect(screen.getByTestId('default-model-select').querySelectorAll('optgroup')).toHaveLength(0)
  })

  it('uzun canlı liste bir seçim listesinde EKSİKSİZ sunuluyor', () => {
    // Eski sekiz çip sınırı bir çip SATIRI içindi; burası bir liste.
    const cok = Array.from({ length: 40 }, (_, i) => M(`m-${i}`, `Model ${i}`, 'anthropic'))
    ciz('anthropic', cok)
    expect(screen.getByText('Model 0')).toBeTruthy()
    expect(screen.getByText('Model 39')).toBeTruthy()
  })

  it('yerel modeller Ollama başlığı altında', () => {
    ciz('ollama', [M('claude-opus-9', 'Claude Opus 9', 'anthropic')],
        { local: [M('qwen3:8b', 'Qwen3 8B', 'ollama')] })
    expect(grup('Ollama')!.textContent).toContain('Qwen3 8B')
    expect(grup('Ollama')!.textContent).not.toContain('Claude Opus 9')
  })
})
