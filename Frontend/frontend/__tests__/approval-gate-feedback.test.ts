/**
 * Onay kartlarının backend yanıtını gerçekten OKUDUĞUNU sınar.
 *
 * Arıza (ölçüldü 2026-07-28): backend onay kapısı bir süre sonra gate kaydını
 * düşürüyor ve geç gelen onaya `{"status":"gate_not_found"}` dönüyor. Frontend
 * yanıt gövdesini hiç okumadan kartı kapatıyordu — kullanıcı onayladığını
 * sanırken işlem çoktan reddedilmişti. Sessiz veri kaybı.
 *
 * Bu testler timeout süresinden BAĞIMSIZ: pencere ne kadar büyürse büyüsün,
 * kaçırıldığında kullanıcının bunu görmesi gerekir.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { renderHook, render, screen, cleanup, fireEvent, act } from '@testing-library/react'

import { useChat } from '../renderer/hooks/home/useChat'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import { MCP_MSG_ID, McpActiveGate } from '../renderer/hooks/home/useMCPApproval'
import { postMcpDecision, decisionToast, gateFailure } from '../renderer/hooks/home/gateResponse'
import axios from 'axios'

// DiffViewer Monaco'yu içeri alıyor; jsdom gerçek editör açamaz ve ölçtüğümüz
// şey editörün görüntüsü değil kararın gidip gitmediği.
vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get }, post, get }
})

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'

/** fetch yanıtını taklit eder: HTTP durumu + JSON gövdesi ayrı ayrı verilebilir. */
const fetchResponse = (body: unknown, httpStatus = 200) => ({
  ok: httpStatus >= 200 && httpStatus < 300,
  status: httpStatus,
  json: async () => body,
})

const setupChat = () => {
  const showToast = vi.fn()
  const { result } = renderHook(() =>
    useChat(
      API,
      { id: 1, sessionToken: 'tok' } as any,
      { provider_type: 'api' } as any,
      null,
      showToast,
      vi.fn(),
      (n: string) => n,
    ),
  )
  return { result, showToast }
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockedAxios.post.mockReset()
  mockedAxios.get.mockReset()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('approveCommand — kayıp gate sessizce yutulmaz', () => {
  it('gate_not_found gelince kullanıcı uyarılır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.approveCommand('g1', true) })

    expect(showToast).toHaveBeenCalledTimes(1)
    const [msg, type] = showToast.mock.calls[0]
    expect(String(msg).length).toBeGreaterThan(0)
    expect(['warning', 'error']).toContain(type)
  })

  it('REDDET yolunda da (approved=false) aynı uyarı çıkar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.approveCommand('g1', false) })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('non-2xx HTTP yanıtı da uyarı üretir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ detail: 'nope' }, 503)))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.approveCommand('g1', true) })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('ağ hatası (fetch throw) uyarı üretir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.approveCommand('g1', true) })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('BAŞARILI yolda gereksiz uyarı ÇIKMAZ', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok', approved: true })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.approveCommand('g1', true) })

    expect(showToast).not.toHaveBeenCalled()
  })

  it('kart her durumda kapanır — asılı kalmaz', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { result } = setupChat()

    act(() => { result.current.setPendingCommand({ command: 'rm -rf /', gateId: 'g1', messageId: 1 }) })
    expect(result.current.pendingCommand).not.toBeNull()

    await act(async () => { await result.current.approveCommand('g1', true) })
    expect(result.current.pendingCommand).toBeNull()
  })
})

describe('answerQuestion — kayıp gate sessizce yutulmaz', () => {
  it('gate_not_found gelince kullanıcı uyarılır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.answerQuestion('q1', { 'Soru?': 'A' }) })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('invalid (gövde şeması reddedildi) da uyarı üretir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'invalid', error: 'answers (dict) gerekli.' })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.answerQuestion('q1', {} as any) })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('BAŞARILI yolda gereksiz uyarı ÇIKMAZ', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' })))
    const { result, showToast } = setupChat()

    await act(async () => { await result.current.answerQuestion('q1', { 'Soru?': 'A' }) })

    expect(showToast).not.toHaveBeenCalled()
  })

  it('kart her durumda kapanır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { result } = setupChat()

    act(() => { result.current.setPendingQuestion({ questions: [], gateId: 'q1', messageId: 1 }) })
    expect(result.current.pendingQuestion).not.toBeNull()

    await act(async () => { await result.current.answerQuestion('q1', { 'Soru?': 'A' }) })
    expect(result.current.pendingQuestion).toBeNull()
  })
})

/**
 * MCP onay kartının GERÇEK karar yolu.
 *
 * ⚠️ Bu blok 2026-07-29'da YENİDEN BAĞLANDI ve sebebi ölçülmüş bir arızadır.
 * Eskiden hook'un `approveMCPFile`/`rejectMCPFile`/`approveMCPDelete`
 * fonksiyonlarını sınıyordu; o dördünün de ÜRÜNDE tek bir çağıranı yoktu (dış
 * denetim `test-only-routing-api-divergence`). Ürün kararı `McpApprovalCards`
 * içinden `postMcpDecision` ile gönderiyor — başka transport, başka sözleşme.
 *
 * Bedeli ölçüldü: 230 testin hepsi yeşilken gerçek kart yolunda
 * `stale-decision-latch` yaşıyordu — kullanıcının bastığı İPTAL yutuluyor,
 * ekrana "Komut iptal edildi" yazılıyor ve KOMUT ÇALIŞIYORDU. Test yanlış
 * yüzeyi ölçtüğü için hiçbir şey kırmızı olmuyordu. Ölü API silindi; bu blok
 * artık ürünün kendi bileşenini çiziyor.
 */
describe('MCP kart kararı — kayıp gate sessizce yutulmaz', () => {
  const GATE: McpActiveGate = { gateId: 'm1', tool: 'bash', workspacePath: '/ws' }

  /** Ürünün kendi kart bileşenini, komut kartı açık halde çizer. */
  const mountCommandCard = (over: Record<string, any> = {}) => {
    const showToast = vi.fn()
    const props: any = {
      gate: GATE,
      workspaceMismatch: false,
      openWorkspacePath: '/ws',
      onResolved: vi.fn(),
      apiBase: API,
      sessionToken: 'tok',
      showToast,
      refreshFileTree: vi.fn(),
      pendingGenFiles: null,
      setPendingGenFiles: vi.fn(),
      pendingDelete: null,
      setPendingDelete: vi.fn(),
      // Setter'lar mock: kart uçuş penceresi boyunca MOUNT KALIYOR. Üründe de
      // öyle — `setPendingCommand(null)` await'ten SONRA çağrılıyor.
      pendingCommand: { command: 'ls', gateId: GATE.gateId, messageId: MCP_MSG_ID },
      setPendingCommand: vi.fn(),
      pendingFix: null,
      setPendingFix: vi.fn(),
      ...over,
    }
    const view = render(React.createElement(McpApprovalCards, props))
    return { showToast, props, view }
  }

  /**
   * Butona `fireEvent` ile basılıyor, `.click()` ile DEĞİL.
   *
   * Sebep ve sınır açıkça yazılı: uçuş sırasında kart ayrıca `fieldset[disabled]`
   * ile kilitleniyor ve jsdom `.click()` çağrısını devre dışı elemanda hiç
   * ateşlemez — yani `.click()` kullansaydık test, mantıksal kilit tamamen
   * silinse bile yeşil kalırdı (görsel kilit onu maskeler). `fireEvent` olayı
   * doğrudan gönderiyor, dolayısıyla ölçülen şey ASIL GÜVENCE: `decide`
   * içindeki karar kilidi.
   */
  const bas = (etiket: string) => fireEvent.click(screen.getByText(etiket))

  it('gate_not_found gelince kullanıcı uyarılır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { showToast } = mountCommandCard()

    await act(async () => { bas('Komutu Çalıştır') })

    expect(showToast).toHaveBeenCalledTimes(1)
    expect(['warning', 'error']).toContain(showToast.mock.calls[0][1])
  })

  it('reddet yolunda da uyarı çıkar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))
    const { showToast } = mountCommandCard()

    await act(async () => { bas('İptal') })

    expect(showToast).toHaveBeenCalledTimes(1)
    expect(['warning', 'error']).toContain(showToast.mock.calls[0][1])
  })

  it('ağ hatası uyarı üretir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')))
    const { showToast } = mountCommandCard()

    await act(async () => { bas('Komutu Çalıştır') })

    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('BAŞARILI yolda YANLIŞ bir uyarı çıkmaz — teslimat mesajı gösterilir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' })))
    const { showToast } = mountCommandCard()

    await act(async () => { bas('İptal') })

    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast.mock.calls[0][0]).toBe('Komut iptal edildi')
    expect(showToast.mock.calls[0][1]).toBe('info')
  })

  it('karar GERÇEKTEN gate\'e gider ve gövdesi yönü taşır', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' }))
    vi.stubGlobal('fetch', fetchMock)
    mountCommandCard()

    await act(async () => { bas('Komutu Çalıştır') })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain(`/mcp-approval-respond/${GATE.gateId}`)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({ approved: true })
  })
})

/**
 * `stale-decision-latch` — dış denetimin tek KRİTİK bulgusu (2026-07-29).
 *
 * Ne olmuştu: `decide` bayrağı POST'tan ÖNCE koyuyor ve bastırılan çağrıya
 * `null` dönüyordu. `null` bu depodaki gate sözleşmesinde "TESLİM EDİLDİ"
 * demek. Sonuç: onay POST'u uçuştayken İptal'e basmak → ret hiç gönderilmiyor →
 * ekrana "Komut iptal edildi" yazılıyor → onay iniyor ve KOMUT ÇALIŞIYOR.
 * Kullanıcının gördüğü son karar ret, gerçekleşen onay.
 *
 * Bu blok İKİ diziyi de ölçüyor, çünkü bulgunun iki ayağı vardı: karşıt seçim
 * ve aynı gate'in yeniden sunulması.
 */
describe('stale-decision-latch — gönderilmemiş karar teslim edilmiş sayılmaz', () => {
  const GATE: McpActiveGate = { gateId: 'latch-1', tool: 'bash', workspacePath: '/ws' }

  const cardProps = (over: Record<string, any> = {}) => ({
    gate: GATE,
    workspaceMismatch: false,
    openWorkspacePath: '/ws',
    onResolved: vi.fn(),
    apiBase: API,
    sessionToken: 'tok',
    showToast: vi.fn(),
    refreshFileTree: vi.fn(),
    pendingGenFiles: null,
    setPendingGenFiles: vi.fn(),
    pendingDelete: null,
    setPendingDelete: vi.fn(),
    pendingCommand: { command: 'rm -rf /', gateId: GATE.gateId, messageId: MCP_MSG_ID },
    setPendingCommand: vi.fn(),
    pendingFix: null,
    setPendingFix: vi.fn(),
    ...over,
  })

  const bas = (etiket: string) => fireEvent.click(screen.getByText(etiket))

  it('onay UÇUŞTAYKEN basılan İptal "iptal edildi" diye raporlanmaz', async () => {
    // Onay POST'u askıda bırakılıyor: gerçek uçuş penceresi bu.
    let cozumle: (v: any) => void = () => {}
    const askida = new Promise((res) => { cozumle = res })
    const fetchMock = vi.fn().mockReturnValue(askida)
    vi.stubGlobal('fetch', fetchMock)

    const props: any = cardProps()
    render(React.createElement(McpApprovalCards, props))

    await act(async () => { bas('Komutu Çalıştır') })   // POST uçuşta, askıda
    await act(async () => { bas('İptal') })             // kullanıcının GÖRÜNÜR reddi

    // (1) İkinci bir POST GİTMEDİ — onay zaten yolda, geri alınamaz.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({ approved: true })

    // (2) ASIL İDDİA: ekranda "iptal edildi" YAZMIYOR. Bulgunun kendisi buydu —
    // komut çalışırken kullanıcıya iptal edildiği söyleniyordu.
    const mesajlar = props.showToast.mock.calls.map((c: any[]) => String(c[0]))
    expect(mesajlar).not.toContain('Komut iptal edildi')
    expect(mesajlar.length).toBe(1)
    expect(props.showToast.mock.calls[0][1]).toBe('warning')

    // Askıdaki onayı çözüp sızıntı bırakmıyoruz.
    await act(async () => {
      cozumle(fetchResponse({ status: 'ok' }))
      await askida
    })
  })

  it('TERS YÖN: karar sonuçlandıktan sonra ikinci basış da yalan söylemez', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' })))
    const props: any = cardProps()
    render(React.createElement(McpApprovalCards, props))

    await act(async () => { bas('Komutu Çalıştır') })
    props.showToast.mockClear()
    await act(async () => { bas('İptal') })

    // Onay çoktan indi; ikinci karar ne gönderiliyor ne de "iptal" diye
    // raporlanıyor. Kilidin kendisi KALIYOR — kaldırmak çift karar demekti.
    expect((globalThis.fetch as any)).toHaveBeenCalledTimes(1)
    const mesajlar = props.showToast.mock.calls.map((c: any[]) => String(c[0]))
    expect(mesajlar).not.toContain('Komut iptal edildi')
    expect(props.showToast).toHaveBeenCalledTimes(1)
  })

  it('teslimatı BAŞARISIZ olan istek yeniden sunulunca yeniden karar VERİLEBİLİR', async () => {
    // Bulgunun ikinci ayağı: bayrak gate id'sine ömür boyu bağlıydı ve bileşen
    // ChatPanel içinde mount kalıyor. Teslimat düştüğünde backend kaydı hâlâ
    // bekliyor olabilir; ikinci sunumda da bastırılırsa kullanıcı o isteğe BİR
    // DAHA karar veremez — sessiz bir kilitlenme.
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetchMock)

    const props: any = cardProps()
    const { rerender } = render(React.createElement(McpApprovalCards, props))
    await act(async () => { bas('İptal') })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Kart ekrandan kalkıyor (gate null) — sunum bitti.
    await act(async () => {
      rerender(React.createElement(McpApprovalCards, { ...props, gate: null }))
    })

    // Aynı gate backend'de hâlâ bekliyor ve yeniden sunuluyor.
    fetchMock.mockResolvedValue(fetchResponse({ status: 'ok' }))
    await act(async () => {
      rerender(React.createElement(McpApprovalCards, props))
    })
    await act(async () => { bas('İptal') })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({ approved: false })
  })
})

/**
 * İKİ-VARYANT KURALI — sınıfın kalan ÜÇ kart yolu.
 *
 * Yukarıdaki blok yalnız KOMUT kartını sürüyor; denetimin kendi probe'u da
 * öyleydi ve bunu açıkça yazmıştı (*"delete, file-creation, and diff controls
 * were source-traced but not all dynamically driven"*). Kod düzeltmesi dördüne
 * de uygulandı ama ÖLÇÜM tek yoldaydı — bu depoda iki kez ölçülmüş kural tam
 * olarak burayı vuruyor: **kapatmayı yazan kişi kapatmanın sınırını göremiyor.**
 *
 * Sınıf tek cümleyle: *gönderilmemiş bir karar, gönderilmiş gibi raporlanamaz.*
 * Her kart tipinde aynı dizi sürülüyor — onay POST'u uçuşta bırakılıyor, sonra
 * karşıt kontrole basılıyor.
 *
 * ⚠️ Kartlar arasında BİR FARK var ve bilerek korunuyor: komut kartının eski
 * hatası YALAN söylemekti ("Komut iptal edildi"), silme/diff kartlarınınki ise
 * SESSİZ kalmaktı (`if (failure)` bastırılan çağrıda hiç ateşlenmiyordu).
 * İkisi de aynı sınıf; ortak ölçüt "ikinci POST yok VE kullanıcı uyarılıyor".
 */
describe('iki-varyant · sınıf diğer üç kart yolunda da kapalı', () => {
  const GATE: McpActiveGate = { gateId: 'v-1', tool: 'write_file', workspacePath: '/ws' }

  /** Uçuşta askıda bırakılan POST + kartı çizen ortak kurulum. */
  const surumeBasla = (kartState: Record<string, any>) => {
    let cozumle: (v: any) => void = () => {}
    const askida = new Promise((res) => { cozumle = res })
    const fetchMock = vi.fn().mockReturnValue(askida)
    vi.stubGlobal('fetch', fetchMock)

    const props: any = {
      gate: GATE,
      workspaceMismatch: false,
      openWorkspacePath: '/ws',
      onResolved: vi.fn(),
      apiBase: API,
      sessionToken: 'tok',
      showToast: vi.fn(),
      refreshFileTree: vi.fn(),
      setCode: vi.fn(),
      setDiffFile: vi.fn(),
      onOpenFile: vi.fn(),
      pendingGenFiles: null,
      setPendingGenFiles: vi.fn(),
      pendingDelete: null,
      setPendingDelete: vi.fn(),
      pendingCommand: null,
      setPendingCommand: vi.fn(),
      pendingFix: null,
      setPendingFix: vi.fn(),
      ...kartState,
    }
    render(React.createElement(McpApprovalCards, props))
    return { props, fetchMock, cozumle, askida }
  }

  const bas = (etiket: string) => fireEvent.click(screen.getByText(etiket))

  /**
   * `onay` uçuşa sokulur, hemen ardından `karsit` kontrole basılır.
   * Ölçüt her kartta aynı: tek POST, ve kullanıcı sessiz bırakılmaz.
   */
  const karsitSecimSinavi = async (
    kartState: Record<string, any>, onay: string, karsit: string,
    /** Onay HÂLÂ uçuştayken koşulacak ek iddialar. */
    ucusPenceresinde?: (props: any) => void,
  ) => {
    const { props, fetchMock, cozumle, askida } = surumeBasla(kartState)

    await act(async () => { bas(onay) })
    props.showToast.mockClear()
    await act(async () => { bas(karsit) })
    ucusPenceresinde?.(props)

    // (1) Karşıt karar GÖNDERİLMEDİ — onay zaten yolda, geri alınamaz.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({ approved: true })

    // (2) Kullanıcı sessiz bırakılmadı ve gönderilmemiş karar "oldu" diye
    //     raporlanmadı. Eski davranış bu iki bacaktan birini kırıyordu.
    expect(props.showToast).toHaveBeenCalledTimes(1)
    expect(props.showToast.mock.calls[0][1]).toBe('warning')

    await act(async () => { cozumle(fetchResponse({ status: 'ok' })); await askida })
    return props
  }

  it('SİLME kartı: onay uçuştayken Vazgeç sessizce yutulmaz', async () => {
    await karsitSecimSinavi(
      { pendingDelete: { path: 'A.cs', messageId: MCP_MSG_ID } },
      'Evet, Dosyayı Sil', 'Vazgeç',
    )
  })

  it('DİFF kartı: onay uçuştayken Reddet sessizce yutulmaz', async () => {
    await karsitSecimSinavi(
      {
        pendingFix: {
          messageId: MCP_MSG_ID, applied: false,
          data: { original_code: 'x', fixed_code: 'y', explanation: 'e', editor_hint: 'A.cs' },
        },
      },
      'Kabul Et', 'Reddet',
      // Diff kartına özel bacak: onay HENÜZ teslim edilmemişken editör tamponu
      // tazelenmemeli — yoksa diskte olmayan bir içerik ekranda gösterilir.
      // ⚠️ İddia UÇUŞ PENCERESİNDE koşuyor. Askı çözüldükten sonra bakmak
      // yanlış olurdu: onay o noktada gerçekten teslim ediliyor ve tamponu
      // tazelemek DOĞRU davranış. (İlk yazımda oraya bakılmıştı ve test,
      // ürünü değil kendi zamanlamasını ölçüyordu.)
      (props) => { expect(props.setCode).not.toHaveBeenCalled() },
    )
  })

  const OLUSTURMA = {
    pendingGenFiles: {
      messageId: MCP_MSG_ID,
      files: [{ name: 'A.cs', code: 'x', suggestedPath: 'A.cs', originalCode: '' }],
    },
  }

  it('OLUŞTURMA kartı: onay uçuştayken İptal sessizce yutulmaz', async () => {
    await karsitSecimSinavi(OLUSTURMA, 'Tümünü Onayla', 'İptal')
  })

  it('OLUŞTURMA kartı: onay uçuştayken ATLA da sessizce yutulmaz', async () => {
    // "Atla" ayrı bir prop'tan (`onSkipOne`) geçiyor ve o yol ayrı ayrı
    // yazılmıştı — sınıfın dördüncü yüzü. Buton yalnız ikon taşıdığı için
    // erişilebilir adı yoktu; ad eklenmeden bu yol ÖLÇÜLEMİYORDU.
    const { props, fetchMock, cozumle, askida } = surumeBasla(OLUSTURMA)

    await act(async () => { bas('Tümünü Onayla') })
    props.showToast.mockClear()
    await act(async () => { fireEvent.click(screen.getByLabelText('Atla')) })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(props.showToast).toHaveBeenCalledTimes(1)
    expect(props.showToast.mock.calls[0][1]).toBe('warning')

    await act(async () => { cozumle(fetchResponse({ status: 'ok' })); await askida })
  })
})

/**
 * Ölü API'nin geri gelmemesi için tripwire.
 *
 * Silinen dört fonksiyon (`approveMCPFile`, `rejectMCPFile`, `approveMCPDelete`,
 * `rejectMCPDelete`) ve `respond` ürünün karar yolunun İKİNCİ bir kopyasıydı;
 * testler o kopyayı sınadığı için gerçek yoldaki kritik bulgu görünmüyordu.
 * Biri geri eklenirse aynı ayrışma yeniden açılır — ve bu depodaki arızaların
 * ortak biçimi tam olarak "uyuşması gereken iki yer uyuşmuyor".
 */
describe('hook ikinci bir karar yolu ihraç ETMEZ', () => {
  it('useMCPApproval yalnız teslim/çözme yüzeyini döner', async () => {
    const { useMCPApproval } = await import('../renderer/hooks/home/useMCPApproval')
    const { result } = renderHook(() =>
      useMCPApproval({
        API,
        enabled: false,
        workspacePath: '/ws',
        setPendingGenFiles: vi.fn(),
        setPendingDelete: vi.fn(),
        setPendingCommand: vi.fn(),
        setPendingFix: vi.fn(),
      }),
    )

    // Liste bilerek TAM eşleşiyor: yeni bir anahtar eklendiğinde bu test
    // patlıyor ve eklenen şeyin ne olduğu gözden geçiriliyor.
    //
    // `gateWorkspaceCheckPending` 31 Ağu 2026'da eklendi ve kapının yasakladığı
    // şey DEĞİL. Yasak olan ikinci bir KARAR yolu (onayla/reddet); bu ise
    // `gateWorkspaceMismatch` ile aynı kategoriden bir GÖSTERGE — karşılaştırma
    // henüz yapılamadığında banner'ın "eşleşiyor" demesini engelliyor.
    // Kararı hâlâ tek yol veriyor.
    //
    // `unknownGates` (26 Sep 2026, parallel chats slice 2) is a LIST of the
    // requests with no known source chat, for the tray. The tray decides
    // through `postMcpDecision` like every card; the hook still exports no
    // way to approve or reject.
    expect(Object.keys(result.current).sort()).toEqual(
      ['activeGate', 'gateWorkspaceCheckPending', 'gateWorkspaceMismatch',
       'openWorkspacePath', 'poll', 'resolveActiveGate', 'unknownGates'].sort(),
    )
  })
})

/**
 * ChatPanel'deki sekiz doğrudan çağrı yerinin ortak motoru. Oradaki hata sadece
 * "sessizce yut" değil, "yuttuktan sonra YEŞİL başarı toast'ı bas"tı.
 */
describe('postMcpDecision + decisionToast — yanlış başarı iddiası üretmez', () => {
  it('gate_not_found → başarı mesajı DEĞİL uyarı gösterilir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'gate_not_found' })))

    const failure = await postMcpDecision(API, 'g1', true, 'tok')
    expect(failure).not.toBeNull()

    const toast = decisionToast(failure, '✅ Dosya oluşturuldu')
    expect(toast.type).not.toBe('success')
    expect(toast.message).not.toContain('✅')
  })

  it('non-2xx → uyarı gösterilir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ detail: 'x' }, 401)))
    const failure = await postMcpDecision(API, 'g1', false, 'tok')
    expect(failure).not.toBeNull()
    expect(decisionToast(failure, '🗑️ Dosya silindi').type).toBe('error')
  })

  it('ağ hatası → uyarı gösterilir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const failure = await postMcpDecision(API, 'g1', true, 'tok')
    expect(failure).not.toBeNull()
  })

  it('TESLİMAT yolunda mesaj AYNEN korunur — ama tip "success" DEĞİL', async () => {
    // ⚠️ Bu test 2026-07-29'da SIKILAŞTIRILDI. Eskiden
    // `toEqual({..., type: 'success'})` bekliyordu; dış doğrulama turu
    // (`mcp-write-premature-success`) o beklentinin kendisinin hatalı olduğunu
    // gösterdi: `failure === null` yalnız backend'in onayı KAYDETTİĞİNİ
    // kanıtlıyor, dosya işlemi bundan sonra köprüde oluyor ve düşebiliyor.
    // Ölçülen yol: hedefin üst dizini normal bir dosyaysa `os.makedirs`
    // FileExistsError atıyor, dosya diske hiç yazılmıyor.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' })))

    const failure = await postMcpDecision(API, 'g1', true, 'tok')
    expect(failure).toBeNull()

    const toast = decisionToast(failure, 'Onayınız gönderildi — dosya yazılıyor')
    expect(toast).toEqual({ message: 'Onayınız gönderildi — dosya yazılıyor', type: 'info' })
  })

  it('teslimat mesajı hiçbir çağrı yerinden "success" yapılamaz', async () => {
    // `successType` parametresi kaldırıldı: dursaydı tek bir çağrı yeri
    // `'success'` geçerek kapıyı geri açardı. Bu test o parametrenin geri
    // gelmesini de yakalar (TS derlemesi + davranış).
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fetchResponse({ status: 'ok' })))
    const failure = await postMcpDecision(API, 'g1', false, 'tok')
    expect(decisionToast(failure, 'Komut iptal edildi')).toEqual({
      message: 'Komut iptal edildi', type: 'info',
    })
    expect((decisionToast as (...a: any[]) => unknown).length).toBe(2)
  })

  it('ağ hatasında belirsizlik postMcpDecision üzerinden de taşınır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const failure = await postMcpDecision(API, 'g1', true, 'tok')
    expect(failure?.uncertain).toBe(true)
  })

  it('gövde JSON değilse (2xx + bozuk gövde) yine başarı SAYILMAZ', () => {
    // Beyaz liste sözleşmesi: yalnız status==='ok' başarıdır. Kara liste
    // kullansaydık backend yeni bir başarısızlık durumu eklediğinde sessizce
    // başarı sayılırdı — düzelttiğimiz hatanın aynısı geri gelirdi.
    expect(gateFailure('mcp', { httpOk: true, httpStatus: 200, body: undefined })).not.toBeNull()
    expect(gateFailure('mcp', { httpOk: true, httpStatus: 200, body: { status: 'yepyeni_hata' } })).not.toBeNull()
  })
})

/**
 * Dış denetim bulgusu (2026-07-28): POST backend tarafından İŞLENDİKTEN SONRA
 * yanıt yolda kaybolursa `fetch` yine istisna atar. UI bunu kesin biçimde
 * "sunucuya ulaşmadı, işlem YAPILMADI — tekrar deneyin" diye raporluyordu.
 * Komut gerçekte çalışmış olabilir; kullanıcı tekrar gönderip aynı komutu İKİ
 * KEZ çalıştırabilir.
 *
 * Önceki düzeltme bir aşırı iddiayı (sessizce başarı say) tersiyle (kesinlikle
 * başarısız say) değiştirmişti. İkisi de yanlış: elde olan bilgi "başarısız"
 * değil, "BİLİNMİYOR". Bu blok üç durumun BİRBİRİNDEN AYIRT EDİLEBİLİR olmasını
 * ölçüyor — üçünün de mesaj üretmesi yetmez.
 */
describe('gateFailure — belirsiz teslimat kesin başarısızlıktan ayrılır', () => {
  /**
   * "İşlem yapılmadı" iddiası mesajda var mı?
   *
   * Neden düz `/yapılmadı/i` DEĞİL: JS'in `i` bayrağı basit case-folding yapıyor
   * ve noktasız `ı` (U+0131) ile ASCII `I`'yı EŞLEŞTİRMİYOR — mesaj "İşlem
   * YAPILMADI" yazdığı için regex sessizce hiçbir şey bulmuyordu (bu testi
   * yazarken ölçüldü). `tr` yerelinde küçültmek doğru eşlemeyi (`I` → `ı`) yapar.
   */
  const yapilmadiIddiasi = (m: string) => m.toLocaleLowerCase('tr').includes('yapılmadı')

  /** Yanıt ALINDI, backend gate'i bulamadı: işlemin yapılmadığı KESİN. */
  const kesin = () => gateFailure('command', {
    httpOk: true, httpStatus: 200, body: { status: 'gate_not_found' },
  })
  /** Yanıt HİÇ alınamadı: işlemin yapılıp yapılmadığı bilinmiyor. */
  const belirsiz = () => gateFailure('command', {
    httpOk: false, error: new Error('ECONNREFUSED'),
  })

  it('ağ istisnası belirsiz olarak işaretlenir', () => {
    expect(belirsiz()?.uncertain).toBe(true)
  })

  it('gate_expired KESİN sayılır — "bilinmiyor" dalına düşmez', () => {
    // Doğrulama turu bulgusu (31 Tem 2026): backend süresi dolmuş bir gate'i
    // REDDEDİLDİ olarak yazıp sebebini gönderiyordu, ama frontend'de karşılığı
    // olmadığı için "tanınmadı: gate_expired" ve "yapılıp yapılmadığı
    // BİLİNMİYOR" deniyordu. Oysa sonuç kesin: bekleyen kalmadığı için işlem
    // yapılmadı. Sebebi eklemenin değeri, onu düşürmemekten geliyor.
    const sonuc = gateFailure('command', {
      httpOk: true, httpStatus: 200,
      body: { status: 'gate_expired', error: 'Onay süresi doldu.' },
    })
    expect(sonuc?.uncertain).not.toBe(true)
    expect(yapilmadiIddiasi(sonuc!.message)).toBe(true)
    expect(sonuc!.message).not.toMatch(/tanınmadı/)
  })

  it('belirsiz mesaj "işlem yapılmadı" DEMEZ — yapılmış olabilir', () => {
    expect(yapilmadiIddiasi(belirsiz()!.message)).toBe(false)
  })

  it('belirsiz mesaj kullanıcıyı körlemesine tekrar göndermeye çağırmaz', () => {
    // "tekrar deneyin" tek başına, çift çalıştırmanın tam olarak nedeni.
    // Beklenti: önce durum kontrolü istensin.
    expect(belirsiz()!.message).toMatch(/kontrol/i)
  })

  it('yanıt alınmış başarısızlık KESİN kalır: belirsiz işaretlenmez', () => {
    // Karşı yön. Her şeyi "belirsiz" saymak, çözdüğümüz sorunun aynadaki hâli
    // olurdu: gerçekten yapılmamış bir işlem için kullanıcı boşuna durum arar.
    expect(kesin()?.uncertain).toBeFalsy()
    expect(yapilmadiIddiasi(kesin()!.message)).toBe(true)
  })

  it('iki durum kullanıcıya AYNI cümleyi göstermez', () => {
    // Testin asıl noktası. İkisi de mesaj üretiyor diye geçen bir test,
    // ayrımın var olduğunu ölçmezdi.
    expect(belirsiz()!.message).not.toBe(kesin()!.message)
  })

  it("status==='ok' hâlâ sessiz — yeni durum başarı yolunu kirletmedi", () => {
    expect(gateFailure('command', { httpOk: true, httpStatus: 200, body: { status: 'ok' } })).toBeNull()
    expect(gateFailure('mcp', { httpOk: true, httpStatus: 200, body: { status: 'ok' } })).toBeNull()
  })

  it('non-2xx yanıt da KESİN başarısızlıktır (yanıt alınmıştır)', () => {
    // Backend yerel (127.0.0.1) ve araya proxy girmiyor: 401/503 doğrudan
    // FastAPI'nin kendi reddi, yani istek işlenmedi. Bunu belirsiz saymak
    // bilgiyi atmak olurdu.
    const f = gateFailure('question', { httpOk: false, httpStatus: 401, body: { detail: 'x' } })
    expect(f?.uncertain).toBeFalsy()
    expect(yapilmadiIddiasi(f!.message)).toBe(true)
  })
})

// ── 2xx + sonuç okunamadı: BELİRSİZ, kesin başarısızlık değil ────────────────
//
// Dış denetim (2026-07-28) `fetch` istisnası dalını belirsize çevirtti, ama bu
// dal "İşlem yapılmadı" demeye devam ediyordu — kapattığımız sınıfın ikinci
// yolu. 2xx alındıysa handler isteği İŞLEMİŞTİR; okunamayan şey sonuçtur.
describe('2xx alındı ama sonuç okunamadı', () => {
  it('gövde ayrıştırılamadığında kesin başarısızlık DEĞİL, belirsiz döner', () => {
    const f = gateFailure('command', { httpOk: true, httpStatus: 200, body: undefined });
    expect(f).not.toBeNull();
    expect(f!.uncertain).toBe(true);
    expect(f!.type).toBe('warning');
    expect(f!.message).not.toMatch(/İşlem yapılmadı/);
    expect(f!.message).toMatch(/BİLİNMİYOR/);
  });

  it('tanınmayan bir durum da belirsizdir ama BAŞARI sayılmaz', () => {
    // Beyaz liste sözleşmesi korunuyor: yalnız 'ok' başarıdır.
    const f = gateFailure('command', { httpOk: true, httpStatus: 200, body: { status: 'expired' } });
    expect(f).not.toBeNull();          // başarı DEĞİL
    expect(f!.uncertain).toBe(true);   // ama "yapılmadı" da denmiyor
    expect(f!.message).toMatch(/expired/);
  });

  it('karşı yön: bilinen kesin başarısızlıklar belirsize KAÇMAZ', () => {
    // gate_not_found ve invalid'de sunucu işlemin yapılmadığını SÖYLÜYOR;
    // onları belirsize katlamak elimizdeki bilgiyi atmak olurdu.
    for (const status of ['gate_not_found', 'invalid']) {
      const f = gateFailure('command', { httpOk: true, httpStatus: 200, body: { status } });
      expect(f!.uncertain).toBeFalsy();
    }
    const nonOk = gateFailure('command', { httpOk: false, httpStatus: 503 });
    expect(nonOk!.uncertain).toBeFalsy();
  });

  it('başarı yolu hâlâ sessiz', () => {
    expect(gateFailure('command', { httpOk: true, httpStatus: 200, body: { status: 'ok' } })).toBeNull();
  });
});
