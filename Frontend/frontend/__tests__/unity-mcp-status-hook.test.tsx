/**
 * `useAIConfig`'in Unity MCP durum sözleşmesi — `blocked` DAHİL.
 *
 * Arıza sınıfı (ölçüldü 2026-07-30): backend `b4065f1`'den beri beşinci bir
 * durum döndürüyor — `blocked`, yani "8080 bizim olmayan bir sunucuda". Hook
 * bunu hiç tanımıyordu: tip dört değerliydi, `reason` alanı okunmuyordu,
 * `turningOn` hesabı `blocked`'da KAPATMA isteği yolluyordu (backend sessizce
 * 200 `{"status":"stopped"}` dönüyor → kullanıcı butona basıyor, hiçbir şey
 * olmuyor, hata da görünmüyor), ve 500 yanıtının `detail`'i — sebebin taşındığı
 * tek yer — sabit bir metinle değiştiriliyordu.
 *
 * Testler durum ADLARINA değil DAVRANIŞA bakıyor: hangi istek gitti, kullanıcıya
 * hangi metin ulaştı, hata sonrası durum tahmin mi edildi yoksa ölçüldü mü.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import axios from 'axios'

import { useAIConfig } from '../renderer/hooks/home/useAIConfig'

vi.mock('axios', () => {
  const get = vi.fn()
  const post = vi.fn()
  const del = vi.fn()
  return { default: { get, post, delete: del }, get, post, delete: del }
})

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'

// Backend'in gerçek metni (`unity_mcp_manager._blocked_reason` →
// `mcp_port_guard.port_busy_message`). Testin bu metne bağlı olması kasıtlı:
// kullanıcının okuyacağı şey bu, ve süreç ADI olmadan sebep eyleme dönmüyor.
const BLOCKED_REASON =
  '8080 portu bize ait olmayan bir süreç tarafından kullanılıyor ' +
  '(node · PID 4242). O süreci kapatıp tekrar deneyin.'

const statusPayload = (status: string, reason: string | null = null) => ({
  data: { status, pid: null, port: 8080, instances: [], reason },
})

// Hook'un beklediği imzayla mock: tipsiz `vi.fn()` `showToast` parametresine
// atanamıyor ve `as any` ile susturmak testin kendi ölçüm aracını körleştirir.
type ShowToast = (msg: string, type: unknown) => void

let showToast: ReturnType<typeof vi.fn<ShowToast>>

const mountHook = () => renderHook(() => useAIConfig(API, null, showToast))

beforeEach(() => {
  showToast = vi.fn<ShowToast>()
  mockedAxios.get.mockReset()
  mockedAxios.post.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('Unity instance projects', () => {
  it('ignores malformed instance entries and clears projects after disconnect or failed polls', async () => {
    mockedAxios.get.mockResolvedValue({ data: { status: 'connected', instances: [
      null, false, 'Game', {}, { project: 7 }, { project: 'GameA' }, { project: 'GameB' },
    ] } })
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpProjects).toEqual(['GameA', 'GameB']))
    mockedAxios.get.mockResolvedValue({ data: { status: 'connected', instances: {} } })
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    expect(result.current.unityMcpProjects).toEqual([])
    mockedAxios.get.mockResolvedValue({ data: { status: 'connected', instances: [{ project: 'GameA' }] } })
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    mockedAxios.get.mockRejectedValue(new Error('offline'))
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    expect(result.current.unityMcpProjects).toEqual([])
    mockedAxios.get.mockResolvedValue({ data: { status: 'off', instances: [{ project: 'stale' }] } })
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    expect(result.current.unityMcpProjects).toEqual([])
  })
})

describe('useAIConfig — blocked durumu', () => {
  it('blocked yanıtı durum olarak geçiyor', async () => {
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('blocked'))
  })

  it('sebep (reason) state e alınıyor — kullanıcı toggle a basmadan önce görebilsin', async () => {
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('blocked'))
    // Süreç adı ve PID sebebin eyleme dönüşen kısmı; onları kaybetmek
    // "port meşgul" demekle aynı şey, yani hiçbir şey söylememek.
    expect(result.current.unityMcpReason).toContain('node')
    expect(result.current.unityMcpReason).toContain('4242')
  })

  it('durum blocked degilse sebep TAŞINMIYOR — bayat sebep gösterilmez', async () => {
    mockedAxios.get.mockResolvedValueOnce(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpReason).toContain('4242'))

    // Kullanıcı yabancı süreci kapattı → sonraki yoklama 'off' diyor.
    mockedAxios.get.mockResolvedValue(statusPayload('off', null))
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    expect(result.current.unityMcpStatus).toBe('off')
    expect(result.current.unityMcpReason).toBeNull()
  })

  it('off yanıtı DOLU bir sebep taşısa bile sunulmuyor — sözleşmeye güvenilmiyor', async () => {
    // Backend `reason`'ı yalnız `blocked` dalında dolduruyor, yani bu senaryo
    // bugün oluşmuyor. Test yine de gerekli: mutasyonla ölçüldü (2026-07-30),
    // sebebi koşulsuz taşıyan bir mutant yalnız `reason: null` gelen
    // senaryolarla sınandığında KAÇIYOR — çünkü null'u taşımak da null yazmak.
    // İddianın ayırt edici biçimi bu: durum blocked değilse sebep YOK.
    mockedAxios.get.mockResolvedValue(statusPayload('off', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('off'))
    expect(result.current.unityMcpReason).toBeNull()
  })

  it('blocked ta toggle AÇMA isteği yolluyor — kapatma DEĞİL', async () => {
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('blocked'))

    mockedAxios.post.mockResolvedValue({ data: { status: 'started', port: 8080 } })
    await act(async () => { await result.current.toggleUnityMcp() })

    // `enabled:false` giderse backend yabancı süreci öldürmüyor ve sessizce
    // 200 `{"status":"stopped"}` dönüyor: kullanıcı için hiçbir şey olmuyor.
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post.mock.calls[0][0]).toContain('/mcp/unity/toggle')
    expect(mockedAxios.post.mock.calls[0][1]).toMatchObject({ enabled: true })
  })

  it('adreslenemeyen calisma alaninda kullaniciya SOYLUYOR — sessiz kalmiyor', async () => {
    // Docker modunda mount disindaki bir klasor backend'e adlandirilamiyor.
    // Sunucu yine kalkiyor ama paket ve autoconnect yazimlari atlaniyor, yani
    // Unity kurulumu YARIM kaliyor. Sessiz birakmak bu depodaki en pahali
    // bicim (denetim, 31 Agu 2026).
    const oncekiIpc = (window as any).ipc
    ;(window as any).ipc = { invoke: vi.fn(async () => '') }   // '' = cevap yok
    try {
      mockedAxios.get.mockResolvedValue(statusPayload('off'))
      const { result } = renderHook(() => useAIConfig(API, null, showToast, '/host/disarisi'))
      await waitFor(() => expect(result.current.unityMcpStatus).toBe('off'))

      mockedAxios.post.mockResolvedValue({ data: { status: 'started', port: 8080 } })
      await act(async () => { await result.current.toggleUnityMcp() })

      const uyarilar = showToast.mock.calls.filter(c => c[1] === 'warning')
      expect(uyarilar.length).toBe(1)
      // Istek yine gidiyor ama yol NULL: yanlis bir yol gondermek, hic
      // gondermemekten kotu olurdu.
      expect(mockedAxios.post.mock.calls[0][1]).toMatchObject({ workspace_path: null })
    } finally {
      ;(window as any).ipc = oncekiIpc
    }
  })

  it('TERS YON: adreslenebilen calisma alaninda uyari YOK', async () => {
    // Kapinin bir sey korudugunun kaniti: yukaridaki test tek basina, her
    // durumda uyari basan bir kodla da gecerdi.
    const oncekiIpc = (window as any).ipc
    ;(window as any).ipc = { invoke: vi.fn(async () => '/workspace') }
    try {
      mockedAxios.get.mockResolvedValue(statusPayload('off'))
      const { result } = renderHook(() => useAIConfig(API, null, showToast, '/host/proje'))
      await waitFor(() => expect(result.current.unityMcpStatus).toBe('off'))

      mockedAxios.post.mockResolvedValue({ data: { status: 'started', port: 8080 } })
      await act(async () => { await result.current.toggleUnityMcp() })

      expect(showToast.mock.calls.filter(c => c[1] === 'warning').length).toBe(0)
      expect(mockedAxios.post.mock.calls[0][1]).toMatchObject({ workspace_path: '/workspace' })
    } finally {
      ;(window as any).ipc = oncekiIpc
    }
  })

  it('connected ta toggle KAPATMA isteği yolluyor — yön korunuyor', async () => {
    // İki yönü de sınamak şart: yalnız "blocked açıyor" iddiası, her durumda
    // `enabled:true` yollayan bir mutasyonu yeşil bırakırdı.
    mockedAxios.get.mockResolvedValue(statusPayload('connected', null))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('connected'))

    mockedAxios.post.mockResolvedValue({ data: { status: 'stopped' } })
    await act(async () => { await result.current.toggleUnityMcp() })
    expect(mockedAxios.post.mock.calls[0][1]).toMatchObject({ enabled: false })
  })
})

describe('useAIConfig — toggle hatasının kullanıcıya ulaşması', () => {
  const settleOff = async (result: { current: { unityMcpStatus: string } }) => {
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('off'))
  }

  it('500 yanıtının detail i mesaja geçiyor — sebep sabit metinle EZİLMİYOR', async () => {
    mockedAxios.get.mockResolvedValue(statusPayload('off', null))
    const { result } = mountHook()
    await settleOff(result as any)

    mockedAxios.post.mockRejectedValue({
      response: { status: 500, data: { detail: BLOCKED_REASON } },
    })
    await act(async () => { await result.current.toggleUnityMcp() })

    expect(showToast).toHaveBeenCalledTimes(1)
    expect(String(showToast.mock.calls[0][0])).toContain('4242')
    expect(result.current.unityMcpError).toContain('4242')
  })

  it('409 un detail i de geçiyor ve yokluğunda Türkçe yedek metin var', async () => {
    mockedAxios.get.mockResolvedValue(statusPayload('off', null))
    const { result } = mountHook()
    await settleOff(result as any)

    mockedAxios.post.mockRejectedValue({ response: { status: 409, data: {} } })
    await act(async () => { await result.current.toggleUnityMcp() })
    expect(String(showToast.mock.calls[0][0])).toContain('Unity')
  })

  it('detail metin DEĞİLSE sabit metne düşüyor (422 dizisi React e basılamaz)', async () => {
    // FastAPI doğrulama hatası `detail`'i bir DİZİ döndürüyor. Doğrudan
    // render edilirse React "Objects are not valid as a React child" atıyor;
    // yani tip kontrolü kozmetik değil, çökme kapısı.
    mockedAxios.get.mockResolvedValue(statusPayload('off', null))
    const { result } = mountHook()
    await settleOff(result as any)

    mockedAxios.post.mockRejectedValue({
      response: { status: 422, data: { detail: [{ loc: ['body'], msg: 'field required' }] } },
    })
    await act(async () => { await result.current.toggleUnityMcp() })

    expect(typeof result.current.unityMcpError).toBe('string')
    expect(result.current.unityMcpError).not.toContain('[object')
    expect(result.current.unityMcpError).toContain('başarısız')
  })

  it('hata sonrası durumu TAHMİN etmiyor, yeniden ÖLÇÜYOR', async () => {
    // Eski davranış: catch içinde koşulsuz `setUnityMcpStatus('off')`.
    // blocked bir portta bu YANLIŞ bilgi — kullanıcı gri "kapalı" görüp
    // tekrar deniyor, oysa sebep duruyor.
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('blocked'))

    mockedAxios.post.mockRejectedValue({
      response: { status: 500, data: { detail: BLOCKED_REASON } },
    })
    await act(async () => { await result.current.toggleUnityMcp() })

    expect(result.current.unityMcpStatus).toBe('blocked')
  })
})

describe('useAIConfig — yoklama başarısızken durum bayat kalmıyor (bulgu I-2)', () => {
  it('başarısız yoklamadan sonra connected KALMIYOR, unknown oluyor', async () => {
    // Ölçülmüş arıza: `catch` dalı yalnız yoklama aralığını değiştiriyordu,
    // durumu ne siliyor ne bayat işaretliyordu. Backend çöktükten sonra bile
    // gösterge SÜRESİZ yeşil kalıyordu — yalan söyleyen bir gösterge,
    // göstergesizlikten kötü. K1/c'nin bütün varlık sebebi yabancı sunucu
    // tespiti; tespit sonucu kullanıcıya ulaşmıyorsa azaltma etkisiz.
    mockedAxios.get.mockResolvedValue(statusPayload('connected'))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('connected'))

    mockedAxios.get.mockRejectedValue(new Error('status endpoint unavailable'))
    await act(async () => { await result.current.refreshUnityMcpStatus() })

    expect(result.current.unityMcpStatus).toBe('unknown')
  })

  it('unknown, off DEĞİL — geçici hata "sunucun kapandı" demek değil', async () => {
    // Ters yön. `off` bir "aç" davetidir; geçici bir ağ hatasında onu göstermek
    // kullanıcıyı sunucusu kapanmış sanmaya iter ve gereksiz bir toggle'a
    // sürükler. Eski `catch` yorumunun bu kısmı haklıydı, eksik olan üçüncü
    // seçenekti.
    mockedAxios.get.mockResolvedValue(statusPayload('connected'))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('connected'))

    mockedAxios.get.mockRejectedValue(new Error('network down'))
    await act(async () => { await result.current.refreshUnityMcpStatus() })

    expect(result.current.unityMcpStatus).not.toBe('off')
    expect(result.current.unityMcpStatus).not.toBe('connected')
  })

  it('bayat blocked SEBEBİ de temizleniyor', async () => {
    // Sebep `blocked` durumuyla yaşıyor. Yoklama koptuğunda durumu bilmiyoruz,
    // dolayısıyla sebebi ekranda bırakmak "port başkasında" diye ölçülmemiş
    // bir iddiayı sürdürmek olurdu.
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpReason).toBe(BLOCKED_REASON))

    mockedAxios.get.mockRejectedValue(new Error('network down'))
    await act(async () => { await result.current.refreshUnityMcpStatus() })

    expect(result.current.unityMcpStatus).toBe('unknown')
    expect(result.current.unityMcpReason).toBeNull()
  })

  it('yoklama düzelince durum geri geliyor — kalıcı bir kilit değil', async () => {
    // Bu olmadan "her hatada unknown'a kilitle" de testi geçerdi ve ürün
    // toparlanamaz hale gelirdi.
    mockedAxios.get.mockRejectedValue(new Error('down'))
    const { result } = mountHook()
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('unknown'))

    mockedAxios.get.mockResolvedValue(statusPayload('connected'))
    await act(async () => { await result.current.refreshUnityMcpStatus() })

    expect(result.current.unityMcpStatus).toBe('connected')
  })
})

describe('useAIConfig — yoklama yarışı (doğrulama turu bulgusu)', () => {
  it('ESKİ istek YENİ sonucu ezmiyor', async () => {
    // İki yoklama uçuştayken önce başlayan sonra dönebiliyor ve bayat durumu
    // geri yazıyordu. Kuşak sayacı olmadan "en son dönen kazanır" oluyordu,
    // oysa "en son BAŞLAYAN kazanmalı".
    let birinciCoz: ((v: unknown) => void) | null = null
    mockedAxios.get.mockImplementationOnce(
      () => new Promise(res => { birinciCoz = res })
    )
    const { result } = mountHook()

    // İkinci yoklama hemen dönüyor ve 'blocked' yazıyor.
    mockedAxios.get.mockResolvedValue(statusPayload('blocked', BLOCKED_REASON))
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    await waitFor(() => expect(result.current.unityMcpStatus).toBe('blocked'))

    // Şimdi BİRİNCİ (eski) istek 'connected' ile çözülüyor — ezmemeli.
    await act(async () => {
      birinciCoz?.(statusPayload('connected'))
      await Promise.resolve()
    })

    expect(result.current.unityMcpStatus).toBe('blocked')
  })

  it('unmount sonrası çözülen istek yeni interval DİRİLTMİYOR', async () => {
    // `useEffect` cleanup'ı stopPolling() çağırıyordu ama uçuştaki istek iptal
    // edilmiyordu; çözüldüğünde catch/then dalları YENİ bir setInterval kuruyor
    // ve yoklama unmount'tan sonra sonsuza kadar sürüyordu. Faz 4'te catch
    // dalına setState eklenmesi bu yolu daha görünür hale getirdi.
    const setInterval1 = vi.spyOn(globalThis, 'setInterval')
    let coz: ((v: unknown) => void) | null = null
    mockedAxios.get.mockImplementation(
      () => new Promise(res => { coz = res })
    )
    const { unmount } = mountHook()
    unmount()
    const oncekiSayi = setInterval1.mock.calls.length

    await act(async () => {
      coz?.(statusPayload('connected'))
      await Promise.resolve()
    })

    expect(setInterval1.mock.calls.length).toBe(oncekiSayi)
    setInterval1.mockRestore()
  })
})
