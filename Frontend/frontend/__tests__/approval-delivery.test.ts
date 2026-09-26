/**
 * Onay kartının TESLİM yolu — kart kullanıcının EKRANINA geliyor mu?
 *
 * Bu dosya, kapının kendisini değil kapıdan öncesini ölçüyor. Ayrım önemli:
 * kart hiç gelmezse köprü 180 sn bekleyip reddediyor (`approval_bridge.py:115`),
 * yani ürün "güvenli" görünür ama KULLANILAMAZ olur. unityMCP mutasyon kapısı
 * sunucuya konmadan önce bu yolun düzelmesi gerekiyor, çünkü kapı konulduğu an
 * bugün hiç kapıya uğramayan 8 sağlayıcı da uğramaya başlıyor.
 *
 * İki kusur 2026-07-29'da kaynaktan ölçüldü:
 *
 *  D1 — polling sağlayıcıya VE `chat.loading`'e bağlıydı. `effectiveProvider ===
 *       'subscription'` olmayan her yolda kart hiç gelmiyordu; CLI sağlayıcıları
 *       sohbet "idle" görünürken de araç çağırdığı için `loading` koşulu abonelik
 *       yolunda bile kart düşürüyordu.
 *
 *  D2 — `pendingFix` (VAR OLAN dosyanın değiştirilmesi) hiçbir yerde render
 *       edilmiyordu. Ölü kod değil: köprü dosya varsa `original`ı DOLU
 *       gönderiyor (`file_tools.py:50-54`) ve hook `setPendingFix`e sapıyor.
 *
 * ⚠️ Bu dosyanın KENDİSİ 2026-07-29 denetiminde dört sahte-yeşil taşıdığı için
 * ölçüldü ve yeniden yazıldı. Düzeltilen sınıflar, tekrar üretilmesin diye:
 *   1. `MCP_MSG_ID` testte ELLE kopyalanmıştı → üretici `-998` yapıldığında
 *      10 testin 10'u yeşil kalıyordu. Artık üreticiden IMPORT ediliyor.
 *   2. "onay gate'e gider" yalnız URL sınıyordu → `approved: true→false`
 *      mutasyonu yeşil kalıyordu. Artık GÖVDE de sınanıyor, iki yönde.
 *   3. "kart yokken boş ekran" yalnız butonun YOKLUĞUNU sınıyordu → erken
 *      return kaldırılınca yeşil kalıyordu. Artık boş ekranın kendi metni de
 *      pozitif olarak aranıyor.
 *   4. Kablolama tripwire'ı süslü parantez SAYIYORDU ve yorumları görmüyordu
 *      (`API, // }` kapanışı erken buluyordu). Artık `typescript` paketinin
 *      kendi ayrıştırıcısı kullanılıyor — yeni bağımlılık değil, zaten
 *      devDependency (tsc bu depoda kapının bir parçası).
 *
 * Testler iki YÖNÜ de ölçüyor. Bu depoda kapıların yalnız "çok dar değil" yönü
 * sınandığı için üç ayrı arıza üretildi; burada "çok geniş değil" yönü de var
 * (kapalıyken polling YAPILMAMALI).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'

// Monaco jsdom'da gerçek editör açamaz; ölçtüğümüz şey diff'in görüntüsü değil
// kartın VARLIĞI ve butonunun ne yaptığı.
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

import axios from 'axios'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { McpApprovalCards } from '../renderer/components/home/McpApprovalCards'
import {
  useMCPApproval,
  MCP_MSG_ID,
  workspaceMismatch,
  McpActiveGate,
} from '../renderer/hooks/home/useMCPApproval'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'

const MSG = {
  id: 1,
  role: 'assistant' as const,
  content: 'merhaba',
  smells: [],
  timestamp: '2026-07-29T00:00:00Z',
}

/** Köprünün var olan bir dosya için ürettiği kart: `original` DOLU. */
const EXISTING_FILE_EDIT = {
  messageId: MCP_MSG_ID,
  applied: false,
  gateId: 'gate123abc',
  data: {
    original_code: 'class Player {}',
    fixed_code: 'class Player { void Jump() {} }',
    explanation: 'MCP: Assets/Scripts/Player.cs güncelleniyor',
    editor_hint: 'Assets/Scripts/Player.cs',
  },
}

/** Kartla birlikte taşınan gate kaydı — artık `window` global'i YOK. */
const GATE: McpActiveGate = {
  gateId: EXISTING_FILE_EDIT.gateId,
  tool: 'write_file',
  workspacePath: '/ws',
}

/** Boş ekran (erken return) metninin kendisi — i18n.tsx:42, varsayılan dil tr. */
const BOS_EKRAN = /Sohbet başlatmak için soldan/

/**
 * ⚠️ ChatPanel.tsx'te `messages.length === 0 && !loading` erken return'ü var:
 * mesajsız fixture'da hiçbir kart render edilmiyor ve test sessizce "geçiyor".
 * Fixture bu yüzden her zaman bir mesaj taşır.
 */
const renderPanel = (overrides: Record<string, any> = {}) => {
  const showToast = vi.fn()
  const props: any = {
    messages: [MSG],
    activeConvId: 1,
    user: { id: 1, name: 'b', sessionToken: 'tok' },
    loading: false,
    clearHistory: vi.fn(),
    lang: 'tr',
    effectiveProvider: 'claude',
    thinkingLevel: 'auto',
    workspacePath: '/ws',
    handleExportToUnity: vi.fn(),
    pendingGenFiles: null,
    setPendingGenFiles: vi.fn(),
    pendingFix: null,
    setPendingFix: vi.fn(),
    openedFilePath: null,
    setCode: vi.fn(),
    refreshFileTree: vi.fn(),
    analyzeProject: vi.fn(),
    openFile: vi.fn(),
    sendMessage: vi.fn(),
    messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn().mockResolvedValue({ success: true }) },
    showToast,
    diffFile: null,
    setDiffFile: vi.fn(),
    pendingDelete: null,
    setPendingDelete: vi.fn(),
    pendingCommand: null,
    setPendingCommand: vi.fn(),
    onApproveCommand: vi.fn().mockResolvedValue(null),
    pendingQuestion: null,
    setPendingQuestion: vi.fn(),
    onAnswerQuestion: vi.fn(),
    deleteFile: vi.fn(),
    setIsTerminalOpen: vi.fn(),
    apiBase: API,
    mcpGate: null,
    mcpWorkspaceMismatch: false,
    mcpOpenWorkspacePath: '/ws',
    onMcpResolved: vi.fn(),
    ...overrides,
  }
  render(React.createElement(ChatPanel, props))
  return { props, showToast: props.showToast }
}

/** MCP düzenleme kartını gate'iyle birlikte kuran kısayol. */
const renderMcpEdit = (overrides: Record<string, any> = {}) =>
  renderPanel({ pendingFix: EXISTING_FILE_EDIT, mcpGate: GATE, ...overrides })

const hookParams = (over: Record<string, any> = {}) => ({
  API,
  enabled: true,
  workspacePath: '/ws',
  setPendingGenFiles: vi.fn(),
  setPendingDelete: vi.fn(),
  setPendingCommand: vi.fn(),
  setPendingFix: vi.fn(),
  ...over,
})

/** `fetch` gövdesini JSON olarak okur. Karar YÖNÜNÜ ölçmek için. */
const postedBody = (fetchMock: ReturnType<typeof vi.fn>, i = 0) =>
  JSON.parse(String(fetchMock.mock.calls[i][1].body))

beforeEach(() => {
  mockedAxios.get.mockReset()
  mockedAxios.post.mockReset()
  mockedAxios.get.mockResolvedValue({ data: { pending: {} } })
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  ;(window as any).__API__ = API
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ── Üreticiye bağlılık ──────────────────────────────────────────────────────
describe('fixture üreticiden okur — elle kopyalanmış sabit yok', () => {
  /**
   * Denetim bulgusu `test-fixture-bypasses-producer` (2026-07-29): bu dosyada
   * `MCP_MSG_ID = -999` ELLE yazılıydı. Üreticiyi `-998` yapmak 10 testin
   * hiçbirini kırmıyordu, çünkü fixture da kart da testin kendi sabitini
   * kullanıyordu. Sabiti export etmek bu sınıfı ürün tarafında kapatmıştı;
   * test tarafında literali kopyalayarak aynı sınıf DİRİLTİLMİŞTİ.
   */
  it('MCP_MSG_ID üreticiden import edilir ve fixture onu kullanır', () => {
    expect(EXISTING_FILE_EDIT.messageId).toBe(MCP_MSG_ID)
    // Değeri de yazıyoruz ki sabit kazara değişirse fark edilsin — ama ölçüt
    // bu değil, yukarıdaki bağ. Bu satır kalksa test hâlâ doğru şeyi ölçer.
    expect(MCP_MSG_ID).toBeLessThan(0)
  })
})

// ── Uçtan uca · hook'un ÜRETTİĞİ kayıt panelin ARADIĞI kayıt mı ─────────────
describe('uçtan uca · köprüden gelen istek gerçekten ekrana çıkar', () => {
  // Uçtan uca ölçüm, ürünün ortamını taklit etmeli: üründe preload HER ZAMAN
  // koşuyor, yani `window.ipc` var. 31 Ağu 2026'da çalışma alanı çevirisi
  // köprü yokken "cevap yok" demeye başladı (bkz. backendWorkspacePath), ve
  // köprüsüz bir düzenek o tarihten sonra uyuşmazlığı hiç ölçemez oldu —
  // ölçtüğü şey artık ürünün davranışı değil, düzeneğin eksiği olurdu.
  // Docker dışı kimlik eşlemesi kuruluyor: ana süreç de tam bunu yapıyor.
  let oncekiIpc: any
  beforeEach(() => {
    oncekiIpc = (window as any).ipc
    ;(window as any).ipc = {
      invoke: vi.fn(async (_kanal: string, arg: string) => arg),
    }
  })
  afterEach(() => { (window as any).ipc = oncekiIpc })

  /**
   * Diğer testler yolun iki ucunu AYRI ölçüyor: hook'a bakanlar setter'ları
   * mock'luyor, panele bakanlar state'i elle veriyor. Aradaki bağ —
   * "hook'un yazdığı `messageId`, panelin aradığı `messageId` ile aynı mı" —
   * hiçbirinde ölçülmüyordu.
   *
   * Bu boşluk mutasyonla bulundu (2026-07-29): `MCP_MSG_ID`'yi değiştirmek
   * hiçbir testi kırmıyordu. O mutasyon eşdeğerdi (sabit tek kaynak), ama
   * eşdeğer OLMAYAN kardeşi — hook'un tek bir dalda başka bir değer yazması —
   * de kırmıyordu. Ürünün asıl teslim yolu tam olarak bu bağ.
   */
  const Harness: React.FC = () => {
    const [genFiles, setGenFiles] = React.useState<any>(null)
    const [del, setDel] = React.useState<any>(null)
    const [cmd, setCmd] = React.useState<any>(null)
    const [fix, setFix] = React.useState<any>(null)
    const mcp = useMCPApproval({
      API,
      enabled: true,
      workspacePath: '/ws',
      setPendingGenFiles: setGenFiles,
      setPendingDelete: setDel,
      setPendingCommand: setCmd,
      setPendingFix: setFix,
    })
    return React.createElement(ChatPanel, {
      messages: [MSG],
      activeConvId: 1,
      user: { id: 1, name: 'b', sessionToken: 'tok' },
      loading: false,
      clearHistory: vi.fn(), lang: 'tr', effectiveProvider: 'claude', thinkingLevel: 'auto',
      workspacePath: '/ws', handleExportToUnity: vi.fn(),
      pendingGenFiles: genFiles, setPendingGenFiles: setGenFiles,
      pendingFix: fix, setPendingFix: setFix,
      pendingDelete: del, setPendingDelete: setDel,
      pendingCommand: cmd, setPendingCommand: setCmd,
      openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(),
      analyzeProject: vi.fn(), openFile: vi.fn(), sendMessage: vi.fn(),
      messagesEndRef: React.createRef<HTMLDivElement>(),
      ipc: { invoke: vi.fn() }, showToast: vi.fn(),
      diffFile: null, setDiffFile: vi.fn(),
      onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
      onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(),
      apiBase: API,
      mcpGate: mcp.activeGate,
      mcpWorkspaceMismatch: mcp.gateWorkspaceMismatch,
      mcpOpenWorkspacePath: mcp.openWorkspacePath,
      onMcpResolved: mcp.resolveActiveGate,
    } as any)
  }

  it('write_file (yeni dosya) isteği karta dönüşür', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g1': { tool: 'write_file', params: { path: 'A.cs', content: 'x' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('Tümünü Onayla')).not.toBeNull()
  })

  it('write_file (var olan dosya) isteği DiffViewer kartına dönüşür', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g2': { tool: 'write_file', params: { path: 'A.cs', content: 'y', original: 'x' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('Kabul Et')).not.toBeNull()
  })

  it('delete_file isteği karta dönüşür', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g3': { tool: 'delete_file', params: { path: 'A.cs' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('Evet, Dosyayı Sil')).not.toBeNull()
  })

  it('bash isteği karta dönüşür', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g4': { tool: 'bash', params: { command: 'ls' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('Komutu Çalıştır')).not.toBeNull()
  })

  it('unityMCP aracı karta dönüşür — ve METNİ Unity diyor, terminal DEMİYOR', async () => {
    // Dış denetim bulgusu (31 Tem 2026, HIGH): kanca yalnız üç eski adı
    // (`write_file`/`delete_file`/`bash`) tanıyordu. Kütükteki 45 Unity
    // aracının HİÇBİRİ onlarla eşleşmiyor, dolayısıyla K1 kapısı kurulduğu an
    // adım modunda her Unity yazması kartsız kalıp 180 sn sonra sessizce
    // reddediliyordu — kullanıcıya hiç sorulmadan.
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g9': { tool: 'manage_gameobject', params: { action: 'delete', name: 'Player' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('İşlemi Çalıştır')).not.toBeNull()
    // Metnin doğru olması güvenliğin parçası: kullanıcı neyi onayladığını
    // bilmeli. Terminal metniyle göstermek yanlış şeyi onaylattırırdı.
    expect(screen.queryByText('Komutu Çalıştır')).toBeNull()
  })

  it('kartın gövdesi HANGİ işlem olduğunu gösteriyor', async () => {
    // Yalnız araç adını göstermek "neyi siliyorum" sorusunu cevapsız bırakır;
    // okunmadan onaylanan bir kart kapının kendisini boşa çıkarır.
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g10': { tool: 'manage_gameobject', params: { action: 'delete', name: 'Player' }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/manage_gameobject/)
    // ⚠️ Kapsam KARTIN GÖVDESİNE daraltılıyor. İlk yazımı yalnız metni
    // arıyordu ve kart hiç render olmazken bile YEŞİLDİ — araç adı ekranda
    // başka bir yerde (gate şeridinde) de geçiyor. Kendi nöbetçim sahte yeşil
    // verdi; `<code>` etiketi kartın gövdesini diğerlerinden ayırıyor.
    expect(govde.tagName.toLowerCase()).toBe('code')
    expect(govde.textContent).toContain('action: delete')
    expect(govde.textContent).toContain('name: Player')
  })

  it('TANINMAYAN bir araç da kartsız KALMIYOR', async () => {
    // Genel dal bilerek "tanınmayan"ı da kapsıyor: yarın eklenen bir araç
    // kartsız kalırsa kapı kullanıcıya ulaşmaz ve istek sessizce ölür.
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g11': { tool: 'gelecekte_eklenen_arac', params: { x: 1 }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('İşlemi Çalıştır')).not.toBeNull()
  })

  it('batch_execute\'un İKİNCİ komutu kartta GİZLENMİYOR', async () => {
    // 2. denetim turu, med: özet her üst düzey değeri tek parça serileştirip
    // 200 karakterde kesiyordu. `commands` tek bir değer olduğu için, ilk
    // komut uzun tutulduğunda ikinci sıradaki SİLME kartta hiç görünmüyor ama
    // onay bütün paketi yetkilendiriyordu — kullanıcı göremediğini onaylıyor.
    const uzun = 'x'.repeat(400)
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g12': { tool: 'batch_execute', params: { commands: [
        { tool: 'manage_asset', params: { action: 'create', name: uzun } },
        { tool: 'manage_asset', params: { action: 'delete', name: 'KRITIK.prefab' } },
      ] }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/batch_execute/)
    expect(govde.tagName.toLowerCase()).toBe('code')
    expect(govde.textContent).toContain('delete')
    expect(govde.textContent).toContain('KRITIK.prefab')
  })

  it('İÇ İÇE batch_execute\'ta da silme GİZLENMİYOR — derinlik sınırı yok', async () => {
    // 3. denetim turu, med: ilk düzeltmede derinlik sınırı (6) vardı ve o,
    // kapattığı sınıfı geri açıyordu — 6. derinlikteki alt ağaç yine tek parça
    // kesiliyor, üstelik uyarı da basılmıyordu. Sunucu iç içe paketleri 8
    // derinliğe kadar sınıflandırıyor, yani şekil erişilebilir.
    const uzun = 'y'.repeat(400)
    const ic = { tool: 'batch_execute', params: { commands: [
      { tool: 'manage_asset', params: { action: 'create', name: uzun } },
      { tool: 'manage_asset', params: { action: 'delete', name: 'DERIN.prefab' } },
    ] } }
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g14': { tool: 'batch_execute', params: { commands: [
        { tool: 'batch_execute', params: { commands: [ic] } },
      ] }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/batch_execute/)
    expect(govde.textContent).toContain('DERIN.prefab')
  })

  it('uzun execute_code gövdesi kesilirse GİZLENEN MİKTAR yazılıyor', async () => {
    // Toplu denetim bulgusu (med): yaprak başına 200 karakterlik kırpma, uzun
    // bir `execute_code` gövdesinin YIKICI kısmını kartta hiç göstermiyordu —
    // kullanıcı zararsız görünen bir başlangıcı onaylıyordu. Sınır büyütüldü
    // ve aşıldığında gizlenen karakter sayısı yazılıyor: sessiz kırpma ile
    // sayılı kırpma arasındaki fark, "burada dahası var mı" sorusunu
    // sorabilmek.
    const kod = 'A'.repeat(4100) + 'System.IO.File.Delete(kritik);'
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g15': { tool: 'execute_code', params: { action: 'execute', code: kod }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/execute_code/)
    expect(govde.textContent).toMatch(/karakter gizlendi/)
  })

  it('4000 karakterin ALTINDAKİ gövde TAM gösteriliyor', async () => {
    // Ters yön: sınır, gerçek yüklerin neredeyse tamamını göstermeli.
    const kod = 'B'.repeat(300) + 'DELETE_MARKER'
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g16': { tool: 'execute_code', params: { action: 'execute', code: kod }, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/execute_code/)
    expect(govde.textContent).toContain('DELETE_MARKER')
    expect(govde.textContent).not.toMatch(/karakter gizlendi/)
  })

  it('kart HANGİ Unity projesinin değişeceğini yazıyor', async () => {
    // Doğrulama turu bulgusu (med): yamanın iki yarısı birbirini iptal
    // ediyordu. Sunucu hedefi `params.unity_instance` olarak ekliyordu, ama
    // buradaki özetleyici tam o anahtarı filtreliyordu ("yönlendirme detayı,
    // kullanıcının kararına girmiyor" gerekçesiyle). Gerekçe yanlıştı: birden
    // fazla Editor bağlıyken "hangi proje" sorusu detay değil, kararın kendisi.
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g17': {
        tool: 'manage_gameobject',
        params: { action: 'delete', name: 'Player', unity_instance: 'ProjeB' },
        workspace_path: '/ws',
      },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    const govde = await screen.findByText(/manage_gameobject/)
    expect(govde.textContent).toContain('ProjeB')
  })

  it('BOZUK parametreli eski araç kartsız KALMIYOR', async () => {
    // 2. denetim turu, low ama etkisi geniş: `params: null` gelince eski dal
    // destructuring'de patlıyordu ve istisna gate KURULDUKTAN sonra düştüğü
    // için `activeGateRef` dolu kalıyordu — yani yalnız o kart değil SONRAKİ
    // BÜTÜN kartlar kayboluyordu.
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g13': { tool: 'write_file', params: null, workspace_path: '/ws' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('İşlemi Çalıştır')).not.toBeNull()
  })

  it('BAŞKA workspace\'ten gelen istek uyarı şeridiyle çıkar', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {
      'g5': { tool: 'delete_file', params: { path: 'A.cs' }, workspace_path: '/baska' },
    } } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(await screen.findByText('⚠ BAŞKA PROJE')).not.toBeNull()
  })

  it('TERS YÖN: bekleyen istek yokken hiçbir kart çıkmaz', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: {} } })
    await act(async () => { render(React.createElement(Harness)) })
    expect(screen.queryByText('Kabul Et')).toBeNull()
    expect(screen.queryByText('Evet, Dosyayı Sil')).toBeNull()
    expect(screen.queryByText('Komutu Çalıştır')).toBeNull()
  })
})

// ── D2 · var olan dosyanın düzenlenmesi ekrana geliyor mu ───────────────────
describe('D2 · MCP "mevcut dosyayı düzenle" kartı ekrana gelir', () => {
  it('pendingFix + gate varken kart RENDER EDİLİR', () => {
    renderMcpEdit()

    // Kartın varlığının kanıtı onay butonu: DiffViewer'ın kendisi (Monaco)
    // stub'lı, ama aksiyon çubuğu gerçek bileşenin kendisi.
    expect(screen.queryByText('Kabul Et')).not.toBeNull()
    expect(screen.queryByText('Reddet')).not.toBeNull()
  })

  it('ONAY gate\'e gider VE gövdesi approved:true taşır', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    renderMcpEdit()
    await act(async () => { screen.getByText('Kabul Et').click() })

    // Kart görünüp butonu ölü olsaydı yukarıdaki test yeşil, ürün bozuk olurdu.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain(`/mcp-approval-respond/${GATE.gateId}`)
    // ⚠️ Denetim bulgusu `test-asserts-endpoint-not-decision`: eskiden yalnız
    // URL sınanıyordu, dolayısıyla `approved: true` → `false` mutasyonu YEŞİL
    // kalıyordu. Kararın YÖNÜ ürünün tek vaadi — adresi değil.
    expect(postedBody(fetchMock)).toEqual({ approved: true })
  })

  it('RET gate\'e gider VE gövdesi approved:false taşır — ters yön', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    renderMcpEdit()
    await act(async () => { screen.getByText('Reddet').click() })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(postedBody(fetchMock)).toEqual({ approved: false })
  })

  it('gate yokken kart ÇIKMAZ — kararsız onay mümkün değil', () => {
    // Yeni tasarımda gate kimliği kartın kendi kaydında taşınıyor, yani
    // "kart var ama gate yok" hali ARTIK ÜRETİLEMEZ. Eskiden üretilebiliyordu
    // (global slot boşken kart duruyordu) ve kullanıcı hiçbir yere gitmeyen
    // bir butona basıyordu.
    renderPanel({ pendingFix: EXISTING_FILE_EDIT, mcpGate: null })
    expect(screen.queryByText('Kabul Et')).toBeNull()
  })

  it('pendingFix yokken kart ÇIKMAZ — kapının çok geniş olmadığının kanıtı', () => {
    renderPanel({ pendingFix: null, mcpGate: GATE })
    expect(screen.queryByText('Kabul Et')).toBeNull()
  })
})

// ── Sahiplik · kartın hangi projeden geldiği yazılır ─────────────────────────
describe('sahiplik · kart hangi workspace\'ten geldiğini söyler', () => {
  /**
   * Denetim bulgusu `missing-owner-check` (HIGH): kayıt `workspace_path`
   * taşıyor, hook onu atıyordu. A workspace'i için açılan kart B'de gösterilip
   * onaylanınca iş `cwd=A` ile koşuyordu.
   *
   * Kullanıcı kararı (2026-07-29): kart GİZLENMEZ, çünkü unityMCP'yi doğrudan
   * başka bir istemciye bağlamış kullanıcıyı 180 sn'lik sessiz redde kilitlerdi.
   * Koruma bilgilendirme: hangi projeden geldiği kartın üstünde yazar.
   */
  it('eşleşen workspace kartta yazar', () => {
    renderMcpEdit()
    expect(screen.queryByText('/ws')).not.toBeNull()
  })

  it('eşleşmeyen workspace UYARIYLA yazar ve açık olanı da gösterir', () => {
    renderMcpEdit({
      mcpGate: { ...GATE, workspacePath: '/baska/proje' },
      mcpWorkspaceMismatch: true,
      mcpOpenWorkspacePath: '/ws',
    })
    expect(screen.queryByText('⚠ BAŞKA PROJE')).not.toBeNull()
    expect(screen.queryByText('/baska/proje')).not.toBeNull()
    expect(screen.queryByText(/açık olan: \/ws/)).not.toBeNull()
  })

  it('eşleşiyorken UYARI çıkmaz — ters yön', () => {
    renderMcpEdit()
    expect(screen.queryByText('⚠ BAŞKA PROJE')).toBeNull()
  })

  it('workspaceMismatch bilinmeyeni uyuşmazlık SAYMAZ', () => {
    // Gate workspace'i boş olabilir (köprünün eski sürümü göndermiyordu) ya da
    // üründe hiç workspace açık olmayabilir. İkisi de "çelişki kanıtlandı"
    // demek değil; bilinmeyeni uyarıya çevirmek gerçek uyarıyı gömerdi.
    expect(workspaceMismatch('', '/ws')).toBe(false)
    expect(workspaceMismatch('/a', null)).toBe(false)
    expect(workspaceMismatch('/a', '/b')).toBe(true)
    // Sondaki eğik çizgi fark değildir.
    expect(workspaceMismatch('/a/', '/a')).toBe(false)
  })
})

// ── D3 · erken return'ler kartı yutmuyor ────────────────────────────────────
describe('D3 · ChatPanel\'in erken return\'leri MCP kartını yutmaz', () => {
  /**
   * Köprü ürünün sohbet durumundan bağımsız çalışıyor: kart, konuşma açılmadan
   * ya da mesaj listesi boşken de gelebiliyor. ChatPanel'in iki erken return'ü
   * (`!activeConvId`, `messages.length === 0 && !loading`) o anda kartı hiç
   * çizmiyordu — kullanıcı hiçbir şey görmeden istek 180 sn'de reddediliyordu.
   */
  it('konuşma açık değilken bile kart gelir', () => {
    renderMcpEdit({ activeConvId: null })
    expect(screen.queryByText('Kabul Et')).not.toBeNull()
  })

  it('mesaj listesi boşken bile kart gelir', () => {
    renderMcpEdit({ messages: [], loading: false })
    expect(screen.queryByText('Kabul Et')).not.toBeNull()
  })

  it('kart YOKKEN konuşmasız hâl hâlâ BOŞ EKRAN çizer — ters yön', () => {
    renderPanel({ activeConvId: null, pendingFix: null, mcpGate: null })
    // ⚠️ Denetim bulgusu `negative-assertion-under-specifies-outcome`: eskiden
    // yalnız "Kabul Et yok" sınanıyordu. Erken return'ü tamamen KALDIRMAK da o
    // iddiayı sağlıyordu (kart zaten yoktu), yani test korumadığı bir şeyi
    // koruyor sanılıyordu. Boş ekranın KENDİSİ pozitif olarak aranıyor.
    expect(screen.queryByText(BOS_EKRAN)).not.toBeNull()
    // Erken return gerçekten koştuysa mesaj listesi hiç çizilmemiştir.
    expect(screen.queryByText('merhaba')).toBeNull()
    expect(screen.queryByText('Kabul Et')).toBeNull()
  })

  it('kart VARKEN boş ekran metni çizilmez — erken return atlanmış olmalı', () => {
    renderMcpEdit({ activeConvId: null })
    expect(screen.queryByText(BOS_EKRAN)).toBeNull()
  })
})

// ── D0 · Docker modunda ad alanı farkı UYUŞMAZLIK DEĞİLDİR ─────────────────
describe('D0 · onay kartı karşılaştırması backend ad alanında yapılır', () => {
  // Gate'in taşıdığı workspace backend'in kendi yolu; Docker modunda bu
  // `/workspace`. Ürünün açık olduğu klasör ise bilerek ana makinenin yolu.
  // İkisini ham karşılaştırmak, Docker'da HER kartı kırmızı uyuşmazlık bandıyla
  // çiziyordu — yani gerçek bir çapraz-proje isteği ayırt edilemez oluyordu
  // (denetim doğrulama turu, 31 Ağu 2026).
  const ipcKur = (esleme: Record<string, string>) => {
    ipcYedekle()
    ;(window as any).ipc = {
      invoke: vi.fn(async (kanal: string, arg: string) =>
        kanal === 'backend-workspace-path' ? (esleme[arg] ?? arg) : arg),
    }
  }

  let oncekiIpc: any
  const ipcYedekle = () => { oncekiIpc = (window as any).ipc }
  afterEach(() => { (window as any).ipc = oncekiIpc })

  it('eşlenen yol gate ile aynıysa uyuşmazlık YOK', async () => {
    ipcKur({ '/host/game': '/workspace' })
    mockedAxios.get.mockResolvedValue({ data: { pending: { g1: {
      tool: 'manage_scene', params: {}, workspace_path: '/workspace' } } } })

    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ workspacePath: '/host/game' }) as any))
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })

    expect(result.current.activeGate?.workspacePath).toBe('/workspace')
    expect(result.current.gateWorkspaceMismatch).toBe(false)
  })

  it('gerçekten başka bir proje ise uyuşmazlık HÂLÂ bildirilir — ters yön', async () => {
    // Kapının hâlâ bir şey koruduğunun kanıtı: yukarıdaki test tek başına,
    // uyuşmazlığı tamamen kapatan bir değişiklikle de geçerdi.
    ipcKur({ '/host/game': '/workspace' })
    mockedAxios.get.mockResolvedValue({ data: { pending: { g2: {
      tool: 'manage_scene', params: {}, workspace_path: '/baska/proje' } } } })

    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ workspacePath: '/host/game' }) as any))
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })

    expect(result.current.gateWorkspaceMismatch).toBe(true)
  })
})

// ── D4 · çeviri SONUÇLANMADAN kart "doğrulandı" gibi görünmez ──────────────
describe('D4 · karşılaştırma yapılamazken kart eşleşme İDDİA ETMEZ', () => {
  /**
   * Dış denetim bulgusu 4 (31 Ağu 2026, med), probe ile doğrulandı.
   *
   * Yoklama ile açık klasörün backend karşılığına çevrilmesi İKİ AYRI effect ve
   * yoklama önce başlıyor. Gerçekten BAŞKA bir projeye ait bir gate, çeviri
   * inmeden çizilebiliyordu: `workspaceMismatch` bilinmeyeni bilerek `false`
   * sayıyor, banner ise o `false`i gri "eşleşiyor" şeridi diye çiziyordu —
   * uyarı yok, "açık olan" satırı yok, onay butonları açık. Saniyenin bir
   * kısmı sonra aynı kart kehribara dönüyordu; kullanıcı o pencerede
   * onaylayabiliyordu.
   *
   * Ölçülen şey "bilinmeyen uyuşmazlık sayılsın" DEĞİL (o karar duruyor,
   * gerekçesi `workspaceMismatch`'in docstring'inde): ölçülen, bilinmeyenin
   * BİLİNEN EŞLEŞME gibi sunulmaması.
   */
  let oncekiIpc: any
  /** Çözümü ÇAĞIRAN tarafta olan IPC — pencere sabit uykuyla değil, elle açılır. */
  const ertelenmisIpc = () => {
    oncekiIpc = (window as any).ipc
    let coz: (v: string) => void = () => {}
    const cevap = new Promise<string>(r => { coz = r })
    ;(window as any).ipc = {
      invoke: vi.fn((kanal: string) =>
        kanal === 'backend-workspace-path' ? cevap : Promise.resolve(null)),
    }
    return { coz: (v: string) => coz(v) }
  }
  afterEach(() => { (window as any).ipc = oncekiIpc })

  const bekle = () => act(async () => { await new Promise(r => setTimeout(r, 10)) })

  /** Hook'u GERÇEK kartla birleştiren düzenek: ölçülen şey banner'ın çizimi. */
  const Harness: React.FC = () => {
    const [cmd, setCmd] = React.useState<any>(null)
    const mcp = useMCPApproval({
      API,
      enabled: true,
      workspacePath: '/host/game',
      setPendingGenFiles: vi.fn(),
      setPendingDelete: vi.fn(),
      setPendingCommand: setCmd,
      setPendingFix: vi.fn(),
    } as any)
    return React.createElement(McpApprovalCards, {
      gate: mcp.activeGate,
      workspaceMismatch: mcp.gateWorkspaceMismatch,
      workspaceCheckPending: mcp.gateWorkspaceCheckPending,
      openWorkspacePath: mcp.openWorkspacePath,
      onResolved: mcp.resolveActiveGate,
      apiBase: API,
      sessionToken: 'tok',
      showToast: vi.fn(),
      refreshFileTree: vi.fn(),
      pendingGenFiles: null, setPendingGenFiles: vi.fn(),
      pendingDelete: null, setPendingDelete: vi.fn(),
      pendingCommand: cmd, setPendingCommand: setCmd,
      pendingFix: null, setPendingFix: vi.fn(),
    } as any)
  }

  const bekleyenBaskaProje = (yol = '/baska/proje') => {
    mockedAxios.get.mockResolvedValue({ data: { pending: { g9: {
      tool: 'manage_scene', params: {}, workspace_path: yol } } } })
  }

  it('çeviri inmeden kart ÇİZİLİR ama "doğrulanıyor" der', async () => {
    const { coz } = ertelenmisIpc()
    bekleyenBaskaProje()

    await act(async () => { render(React.createElement(Harness)) })
    await bekle()

    // Kart görünmeli: beklemek de gizlemektir ve gizlenen istek köprüde
    // 180 sn sonra sessizce reddediliyor.
    expect(screen.queryByText('İşlemi Çalıştır')).not.toBeNull()
    // Ve şerit eşleşme İDDİA ETMEMELİ.
    expect(screen.queryByText('PROJE DOĞRULANIYOR')).not.toBeNull()

    coz('/workspace')
    await bekle()

    // Çeviri indi: aynı kart artık gerçek uyarısını gösteriyor.
    expect(screen.queryByText('PROJE DOĞRULANIYOR')).toBeNull()
    expect(screen.queryByText('⚠ BAŞKA PROJE')).not.toBeNull()
  })

  it('eşleşen projede pencere kapanınca "doğrulanıyor" DA kalkar — ters yön', async () => {
    // Bayrağı sonsuza dek açık bırakan bir düzeltme, üstteki testi tek başına
    // geçerdi: banner o zaman hiçbir zaman eşleşme söylemez, yani şerit
    // kullanıcıya hiçbir şey öğretmez olurdu.
    const { coz } = ertelenmisIpc()
    bekleyenBaskaProje('/workspace')

    await act(async () => { render(React.createElement(Harness)) })
    await bekle()
    expect(screen.queryByText('PROJE DOĞRULANIYOR')).not.toBeNull()

    coz('/workspace')
    await bekle()

    expect(screen.queryByText('PROJE DOĞRULANIYOR')).toBeNull()
    expect(screen.queryByText('⚠ BAŞKA PROJE')).toBeNull()
    expect(screen.queryByText('/workspace')).not.toBeNull()
  })

  it('hook: pencere boyunca beklemede, sonrasında uyuşmazlık', async () => {
    const { coz } = ertelenmisIpc()
    bekleyenBaskaProje()

    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ workspacePath: '/host/game' }) as any))
    await bekle()

    expect(result.current.activeGate?.gateId).toBe('g9')
    expect(result.current.gateWorkspaceCheckPending).toBe(true)
    // Bilinmeyen HÂLÂ uyuşmazlık değil — o karar değişmedi.
    expect(result.current.gateWorkspaceMismatch).toBe(false)

    coz('/workspace')
    await bekle()

    expect(result.current.gateWorkspaceCheckPending).toBe(false)
    expect(result.current.gateWorkspaceMismatch).toBe(true)
  })

  it('gate workspace BİLDİRMEDİYSE beklemede denmez — karşılaştırılacak şey yok', async () => {
    // Köprünün eski sürümü `workspace_path` göndermiyor. Orada çeviri inse de
    // inmese de bir karşılaştırma doğmaz; "doğrulanıyor" demek, hiç gelmeyecek
    // bir cevabı bekliyormuş gibi yapmak olurdu.
    ertelenmisIpc()
    bekleyenBaskaProje('')

    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ workspacePath: '/host/game' }) as any))
    await bekle()

    expect(result.current.activeGate).not.toBeNull()
    expect(result.current.gateWorkspaceCheckPending).toBe(false)
  })

  it('açık proje DEĞİŞİNCE eski projenin cevabı "doğrulandı" sayılmaz', async () => {
    // Mutasyonla bulundu (31 Ağu 2026): bayrağı "cevap geldi mi" diye tutmak
    // ikinci bir pencere bırakıyor — kullanıcı projeyi değiştirdiğinde ELDEKİ
    // cevap ÖNCEKİ projeye ait, ama karşılaştırma yapılmış gibi görünüyor.
    // Bu yüzden state cevabın HANGİ yol için alındığını da taşıyor.
    oncekiIpc = (window as any).ipc
    ;(window as any).ipc = {
      invoke: vi.fn(async (_kanal: string, yol: string) =>
        yol === '/host/game' ? '/workspace' : new Promise<string>(() => {})),
    }
    bekleyenBaskaProje('/workspace')

    const { result, rerender } = renderHook(
      (props: any) => useMCPApproval(hookParams(props) as any),
      { initialProps: { workspacePath: '/host/game' } })
    await bekle()
    expect(result.current.gateWorkspaceCheckPending).toBe(false)

    await act(async () => { rerender({ workspacePath: '/host/other' }) })
    // Cevap YOLDA: yeni projenin backend karşılığı henüz bilinmiyor.
    expect(result.current.gateWorkspaceCheckPending).toBe(true)
    expect(result.current.gateWorkspaceMismatch).toBe(false)
  })

  /**
   * ⚠️ Denetim bulgusu `wiring-gate-reads-comments` (31 Ağu 2026): bu test
   * eskiden `readFileSync` + `toContain` ile ÜÇ alt dize arıyordu. Propları
   * JSX yorumuna taşımak (`{/* workspaceCheckPending={mcp.gateWorkspaceCheckPending} *\/}`)
   * üçünü de metinde bıraktığı için test YEŞİL kalıyordu ve hiçbir karta
   * `workspaceCheckPending` ulaşmıyordu — dize aramak yorumla kodu ayırt
   * etmiyor. Düzeltme iki bacağı AYRI yöntemle kapatıyor:
   *
   *  - ChatPanel → McpApprovalCards bacağı GERÇEKTEN RENDER EDİLİYOR: prop
   *    veriliyor, banner metni ekranda aranıyor. Yorum satırındaki bir prop
   *    gerçek DOM'a hiçbir zaman ulaşamaz, o yüzden bu ikisi yorumla
   *    kandırılamaz.
   *  - home.tsx bacağı render EDİLEMİYOR (Electron IPC + auth + Monaco birden
   *    gerekiyor — dosyadaki diğer AST testleriyle aynı sınır). Onun yerine
   *    `typescript` paketinin ayrıştırıcısıyla İLGİLİ JSX elemanının
   *    ATTRIBUTE düğümü aranıyor: bir yorumun İÇİNDEKİ metin ayrıştırıcı
   *    tarafından hiç JsxAttribute üretmez, yani "prop yorumda" hâli burada
   *    `null` döner ve test KIRMIZI olur — dize aramanın aksine.
   *
   * SINIR: AST kontrolü, kodun VAR OLDUĞUNU ve doğru elemana bağlı olduğunu
   * kanıtlıyor; home.tsx'in gerçekten mount edilip `mcp.gateWorkspaceCheckPending`
   * değerinin doğru aktığını kanıtlamıyor (bu depoda o mount hiçbir testte
   * yapılamıyor). Bu bir DOĞRULUK kanıtı değil, "prop kablosu koptu mu"
   * tripwire'ı — üstteki ChatPanel render'ı ile karıştırılmasın.
   */
  it('ChatPanel → McpApprovalCards: workspaceCheckPending GERÇEKTEN render edilir', () => {
    renderMcpEdit({ mcpWorkspaceCheckPending: true, mcpWorkspaceMismatch: false })
    expect(screen.queryByText('PROJE DOĞRULANIYOR')).not.toBeNull()
  })

  it('ters yön: prop verilmezse "doğrulanıyor" hiç çizilmez', () => {
    renderMcpEdit()
    expect(screen.queryByText('PROJE DOĞRULANIYOR')).toBeNull()
  })

  it('home.tsx: iki geçiş noktası GERÇEK JSX attribute\'una bağlı — ast ile, dize ile değil', () => {
    const findJsxElement = (sf: ts.SourceFile, name: string): ts.JsxOpeningLikeElement | null => {
      let found: ts.JsxOpeningLikeElement | null = null
      const visit = (n: ts.Node): void => {
        if ((ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) &&
            ts.isIdentifier(n.tagName) && n.tagName.text === name) found = n
        ts.forEachChild(n, visit)
      }
      visit(sf)
      return found
    }
    // Attribute düğümünü BULUYOR, dize aramıyor: yorum içindeki bir prop hiç
    // JsxAttribute üretmediği için burada `null` döner.
    const jsxAttrExpr = (el: ts.JsxOpeningLikeElement, attrName: string): string | null => {
      for (const p of el.attributes.properties) {
        if (ts.isJsxAttribute(p) && p.name.getText() === attrName) {
          return p.initializer && ts.isJsxExpression(p.initializer) && p.initializer.expression
            ? p.initializer.expression.getText()
            : null
        }
      }
      return null
    }

    const path = join(__dirname, '..', 'renderer', 'pages', 'home.tsx')
    const src = readFileSync(path, 'utf-8')
    const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

    const cards = findJsxElement(sf, 'McpApprovalCards')
    expect(cards, 'home.tsx McpApprovalCards elemanını içermiyor').not.toBeNull()
    expect(jsxAttrExpr(cards!, 'workspaceCheckPending')).toBe('mcp.gateWorkspaceCheckPending')

    const panel = findJsxElement(sf, 'ChatPanel')
    expect(panel, 'home.tsx ChatPanel elemanını içermiyor').not.toBeNull()
    expect(jsxAttrExpr(panel!, 'mcpWorkspaceCheckPending')).toBe('mcp.gateWorkspaceCheckPending')
  })
})

// ── D1 · polling sağlayıcıdan ve loading'den bağımsız ───────────────────────
describe('D1 · kart yoklaması sağlayıcıdan ve sohbet durumundan bağımsızdır', () => {
  it('sohbet BOŞTAYKEN de yoklama yapar', async () => {
    vi.useFakeTimers()
    renderHook(() => useMCPApproval(hookParams() as any))

    await act(async () => { await vi.advanceTimersByTimeAsync(1200) })

    // Eski davranış: `loading` false olduğu için setInterval hiç kurulmuyordu.
    // CLI sağlayıcıları sohbet idle görünürken araç çağırdığı için kart ölüyordu.
    expect(mockedAxios.get).toHaveBeenCalled()
    expect(String(mockedAxios.get.mock.calls[0][0])).toContain('/mcp-pending')
  })

  it('ilk yoklama bir saniye BEKLEMEZ', async () => {
    vi.useFakeTimers()
    renderHook(() => useMCPApproval(hookParams() as any))

    // Köprü ürün penceresinden bağımsız çalışıyor: mount anında zaten açık bir
    // gate olabilir. Yalnız setInterval'e güvenmek onu bir tam saniye
    // geciktirirdi.
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('yoklama aralığı 1 sn — 7 ms\'lik bir aralıkla karışmaz', async () => {
    // Eski hâlinde bu suite 1000 ms yerine 7 ms aralıkla da yeşil kalıyordu,
    // yani aralığı hiç sabitlemiyordu (denetim notu). Sayarak sabitliyoruz:
    // 2500 ms'de mount yoklaması + 2 interval = 3.
    vi.useFakeTimers()
    renderHook(() => useMCPApproval(hookParams() as any))

    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })

    expect(mockedAxios.get).toHaveBeenCalledTimes(3)
  })

  it('enabled=false iken YOKLAMA YAPMAZ — ters yön', async () => {
    vi.useFakeTimers()
    renderHook(() => useMCPApproval(hookParams({ enabled: false }) as any))

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })

    // `enabled` artık sağlayıcı değil "backend hazır + token kurulu" demek.
    // Kurulmadan yoklamak her saniye sessiz 401 üretirdi.
    expect(mockedAxios.get).not.toHaveBeenCalled()
  })
})

// ── Kuyruk · aynı anda tek kart, kaybolan istek yok ──────────────────────────
describe('kuyruk · iki bekleyen istek birbirini EZMEZ', () => {
  /**
   * Denetim bulguları `single-slot-approval-loss` ve `approval-gate-misbinding`
   * (HIGH). Eskiden her yoklamada bekleyenlerin HEPSİ işleniyor, hepsi aynı tek
   * kart slotuna yazıyor ve sonuncusu öncekilerin üstüne biniyordu; üstü çizilen
   * istek "görüldü" işaretlendiği için bir daha HİÇ gösterilmiyordu. Ayrıca
   * gate kimliği `window.__mcpWriteGate` global'inde tekti: "Yeni dosya"
   * kartına basmak ÖTEKİ isteğin gate'ini onaylıyordu.
   */
  const IKI_BEKLEYEN = {
    'g-create': { tool: 'write_file', params: { path: 'A.cs', content: 'a' }, workspace_path: '/ws' },
    'g-modify': { tool: 'write_file', params: { path: 'B.cs', content: 'b', original: 'eski' }, workspace_path: '/ws' },
  }

  it('ilk yoklamada YALNIZ bir kart kurulur', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: IKI_BEKLEYEN } })
    const setPendingGenFiles = vi.fn()
    const setPendingFix = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, setPendingGenFiles, setPendingFix }) as any))

    await act(async () => { await result.current.poll() })

    expect(setPendingGenFiles).toHaveBeenCalledTimes(1)
    expect(setPendingFix).not.toHaveBeenCalled()
    expect(result.current.activeGate?.gateId).toBe('g-create')
  })

  it('karar verilmeden ikinci yoklama İKİNCİ kartı kurmaz', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: IKI_BEKLEYEN } })
    const setPendingGenFiles = vi.fn()
    const setPendingFix = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, setPendingGenFiles, setPendingFix }) as any))

    await act(async () => { await result.current.poll() })
    await act(async () => { await result.current.poll() })
    await act(async () => { await result.current.poll() })

    expect(setPendingGenFiles).toHaveBeenCalledTimes(1)
    expect(setPendingFix).not.toHaveBeenCalled()
  })

  it('karar verilince SIRADAKİ kart gelir — istek kaybolmuyor', async () => {
    mockedAxios.get.mockResolvedValue({ data: { pending: IKI_BEKLEYEN } })
    const setPendingGenFiles = vi.fn()
    const setPendingFix = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, setPendingGenFiles, setPendingFix }) as any))

    await act(async () => { await result.current.poll() })
    act(() => { result.current.resolveActiveGate() })
    // Karar verilen gate backend'de artık bekleyenler arasında değil.
    mockedAxios.get.mockResolvedValue({ data: { pending: { 'g-modify': IKI_BEKLEYEN['g-modify'] } } })
    await act(async () => { await result.current.poll() })

    expect(setPendingFix).toHaveBeenCalledTimes(1)
    expect(result.current.activeGate?.gateId).toBe('g-modify')
  })

  it('açık kartın gate\'i backend\'den DÜŞERSE kart kaldırılır — kalıcı kilit yok', async () => {
    // Köprünün TTL süpürmesi gate'i düşürebiliyor. Kart ekranda kalıp
    // `activeGate` dolu kalsaydı sonraki BÜTÜN istekler bloke olurdu; bu,
    // kaldırdığımız kilitten daha kötü olurdu.
    mockedAxios.get.mockResolvedValue({ data: { pending: { 'g-1': IKI_BEKLEYEN['g-create'] } } })
    const setPendingGenFiles = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, setPendingGenFiles }) as any))

    await act(async () => { await result.current.poll() })
    expect(result.current.activeGate).not.toBeNull()

    mockedAxios.get.mockResolvedValue({ data: { pending: {} } })
    await act(async () => { await result.current.poll() })

    expect(result.current.activeGate).toBeNull()
    expect(setPendingGenFiles).toHaveBeenLastCalledWith(null)
  })

  it('gate kimliği kart kurulurken ZATEN hazırdır', async () => {
    // Eskiden kimlik `window` global'ine yazılıyordu ve sıra yalnız React
    // batching sayesinde tutuyordu; batching'e bağlı doğruluk doğruluk değil.
    mockedAxios.get.mockResolvedValue({
      data: { pending: { 'del-1': { tool: 'delete_file', params: { path: 'P.cs' }, workspace_path: '/ws' } } },
    })
    let gateAtRender: unknown = 'HİÇ-ÇAĞRILMADI'
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({
        enabled: false,
        setPendingDelete: vi.fn(() => { gateAtRender = (result as any).current?.activeGate ?? 'YOK' }),
      }) as any))

    await act(async () => { await result.current.poll() })

    expect(result.current.activeGate).toEqual({ gateId: 'del-1', tool: 'delete_file', workspacePath: '/ws' })
  })
})

// ── Sessiz arıza · yoklama düşerse kullanıcı öğrenir ────────────────────────
describe('yoklama kalıcı olarak düşerse kullanıcı UYARILIR', () => {
  /**
   * Denetim bulgusu `silent-approval-delivery-failure`: `poll()`'un `catch`'i
   * boştu ve yoklama artık koşulsuz koştuğu için kalıcı bir 401/403 (yanlış
   * token) hiçbir iz bırakmıyordu — ne kart, ne toast, ne konsol. Kullanıcı
   * kartı bekliyor, köprü 180 sn sonra reddediyor, ekranda hiçbir şey yok.
   */
  it('5 ardışık hatadan sonra tek bir uyarı basılır', async () => {
    mockedAxios.get.mockRejectedValue(new Error('Request failed with status code 401'))
    const showToast = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, showToast }) as any))

    for (let i = 0; i < 4; i++) await act(async () => { await result.current.poll() })
    expect(showToast).not.toHaveBeenCalled()   // erken gürültü yok

    await act(async () => { await result.current.poll() })
    expect(showToast).toHaveBeenCalledTimes(1)

    // Her saniye tekrar basmaz — uyarı bilgidir, işkence değil.
    for (let i = 0; i < 5; i++) await act(async () => { await result.current.poll() })
    expect(showToast).toHaveBeenCalledTimes(1)
  })

  it('İLK hatada konsola yazar — geliştirici sinyali eşiği beklemez', async () => {
    // Toast eşiğe bağlı (gürültü olmasın), konsol değil. Denetim probe'u
    // "ne toast ne konsol ne kart" diye ölçüyordu; eşiğin altındaki her hata
    // hâlâ tamamen sessizdi ve bir arızayı teşhis etmenin tek yolu ekrana
    // toast'ın düşmesini beklemek olurdu.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockedAxios.get.mockRejectedValue(new Error('401'))
    const showToast = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, showToast }) as any))

    await act(async () => { await result.current.poll() })

    expect(warn).toHaveBeenCalledTimes(1)
    expect(showToast).not.toHaveBeenCalled()
  })

  it('araya giren BAŞARILI yoklama sayacı sıfırlar — geçici kesinti sessiz', async () => {
    const showToast = vi.fn()
    const { result } = renderHook(() =>
      useMCPApproval(hookParams({ enabled: false, showToast }) as any))

    mockedAxios.get.mockRejectedValue(new Error('boom'))
    for (let i = 0; i < 4; i++) await act(async () => { await result.current.poll() })
    mockedAxios.get.mockResolvedValue({ data: { pending: {} } })
    await act(async () => { await result.current.poll() })
    mockedAxios.get.mockRejectedValue(new Error('boom'))
    for (let i = 0; i < 4; i++) await act(async () => { await result.current.poll() })

    expect(showToast).not.toHaveBeenCalled()
  })
})

// ── Kablolama tripwire'ı ────────────────────────────────────────────────────
describe('kablolama · çağrı yeri kartı sağlayıcıya geri bağlamaz', () => {
  /**
   * ⚠️ Bunun NE OLMADIĞI: doğruluk kanıtı değil. home.tsx'i mount etmek
   * (Electron IPC, auth, workspace, Monaco) bu suite'te makul değil, o yüzden
   * ölçülen şey çağrı yerinin KAYNAĞI. Yaptığı tek şey sessiz bir geri dönüşü
   * gürültülü yapmak — kütük pin'iyle aynı sınıf araç, aynı sınırla.
   *
   * ⚠️ Ayrıştırma neden elle DEĞİL: önceki hâli süslü parantez sayıyordu ve
   * yorumları görmüyordu. `API, // }` yazan tek satır sayacı erken kapatıyor,
   * geri kalan blok (yani sağlayıcı koşulunun konabileceği yer) hiç
   * incelenmiyordu — tsc'den geçen, testten de geçen bir geri dönüş mümkündü
   * (denetim bulgusu `text-parser-false-green`). `typescript` paketi zaten
   * devDependency; yeni bağımlılık YOK.
   */
  const callArgSource = () => {
    const path = join(__dirname, '..', 'renderer', 'pages', 'home.tsx')
    const src = readFileSync(path, 'utf-8')
    const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let call: ts.CallExpression | null = null
    const visit = (n: ts.Node): void => {
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'useMCPApproval') {
        call = n
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
    return call as ts.CallExpression | null
  }

  it('home.tsx useMCPApproval\'ı hâlâ çağırıyor — testin öncülü', () => {
    expect(callArgSource(), 'home.tsx artık useMCPApproval çağırmıyor').not.toBeNull()
  })

  it('çağrı argümanında sağlayıcı/loading koşulu YOK', () => {
    const call = callArgSource()!
    const arg = call.arguments[0]
    expect(ts.isObjectLiteralExpression(arg)).toBe(true)

    // Yorumlar ayrıştırıcı tarafından zaten atılmış; burada yalnız GERÇEK
    // kod düğümleri geziliyor.
    const identifiers: string[] = []
    const strings: string[] = []
    const gez = (n: ts.Node): void => {
      if (ts.isIdentifier(n)) identifiers.push(n.text)
      if (ts.isStringLiteral(n)) strings.push(n.text)
      ts.forEachChild(n, gez)
    }
    gez(arg)

    expect(identifiers).not.toContain('effectiveProvider')
    expect(strings).not.toContain('subscription')
    // `chat.loading`: `loading` adlı bir tanımlayıcı geçmemeli. `isLoading`
    // ayrı bir isimdir ve geçmesi BEKLENİR (`enabled: !auth.isLoading`).
    expect(identifiers).not.toContain('loading')
    expect(identifiers).toContain('isLoading')
  })

  it('sahiplik bilgisi çağrıya BAĞLI — workspacePath geçiliyor', () => {
    const call = callArgSource()!
    const arg = call.arguments[0] as ts.ObjectLiteralExpression
    const keys = arg.properties
      .map(p => (p.name && ts.isIdentifier(p.name) ? p.name.text : ''))
      .filter(Boolean)
    expect(keys).toContain('enabled')
    expect(keys).toContain('workspacePath')
  })

  it('workspace SEÇİLMEMİŞ dalı da kartı çiziyor', () => {
    /**
     * Denetim bulgusu `approval-card-hidden-by-view-state`: `home.tsx`
     * workspace yokken ChatPanel yerine `WorkspaceScreen` döndürüyor, yani
     * kartlar hiç mount edilmiyordu. Köprü ürünün görünüm durumunu bilmediği
     * için istek state'e girip ekrana çıkmadan 180 sn'de reddediliyordu.
     *
     * Neden kaynak ölçülüyor, `home.tsx` mount edilmiyor: mount Electron IPC,
     * auth, workspace ve Monaco'yu birden ister — bu suite'te makul değil.
     * Sınırı açıkça yazıyoruz: bu bir DOĞRULUK kanıtı değil, dalın sessizce
     * geri alınmasını gürültülü yapan bir tripwire.
     */
    const path = join(__dirname, '..', 'renderer', 'pages', 'home.tsx')
    const src = readFileSync(path, 'utf-8')
    const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

    let dal: ts.IfStatement | null = null
    const visit = (n: ts.Node): void => {
      if (ts.isIfStatement(n) && n.expression.getText().replace(/\s/g, '') === '!fs.workspacePath') {
        dal = n
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
    expect(dal, 'home.tsx\'te `!fs.workspacePath` dalı yok — bu testin öncülü çöktü').not.toBeNull()

    const jsxAdlari: string[] = []
    const gez = (n: ts.Node): void => {
      if ((ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) && ts.isIdentifier(n.tagName)) {
        jsxAdlari.push(n.tagName.text)
      }
      ts.forEachChild(n, gez)
    }
    gez((dal as unknown as ts.IfStatement).thenStatement)

    expect(jsxAdlari).toContain('WorkspaceScreen')
    expect(jsxAdlari).toContain('McpApprovalCards')
  })

  it('kart geldiğinde panel AÇILIR ve karta KAYDIRILIR', () => {
    /**
     * `approval-card-hidden-by-view-state` üç bacaklıydı; workspace ekranı
     * bunlardan yalnız biri. Diğer ikisi: (a) sohbet paneli kapalıyken kart
     * 0 piksel genişlikte çiziliyor, (b) kart mesaj listesinin dışında
     * olduğu için `messages`/`loading` değişmiyor ve hiçbir şey ona
     * kaydırmıyor — uzun sohbette kart ekranın altında kalıyor.
     *
     * ⚠️ SINIR: bu da bir tripwire, doğruluk kanıtı değil. `home.tsx` mount
     * edilemiyor (Electron IPC + auth + Monaco). Ölçtüğü şey iki effect'in
     * `mcp.activeGate`'e BAĞLI olduğu; effect'in gerçekten çalıştığı değil.
     * Denetim probe'u da bunu ölçemedi: `useMCPApproval`'ı stub'ladığı için
     * `activeGate` orada hiç doğmuyor.
     */
    const path = join(__dirname, '..', 'renderer', 'pages', 'home.tsx')
    const src = readFileSync(path, 'utf-8')
    const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

    const gateEffektleri: string[] = []
    const visit = (n: ts.Node): void => {
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'useEffect') {
        const deps = n.arguments[1]
        if (deps && deps.getText().includes('mcp.activeGate')) {
          gateEffektleri.push(n.arguments[0].getText())
        }
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)

    // `scrollIntoView` is no longer called here directly: all three copies of
    // the auto-scroll pattern moved into `useAutoScroll`, and the gate branch
    // uses its FORCED variant (`scrollToBottom`) — the one that ignores whether
    // the user has scrolled up. `followIfPinned` deliberately does not count:
    // it would leave the card unscrolled-to exactly when the user is reading
    // higher up, which is the fault this tripwire exists for.
    expect(gateEffektleri.some(b => b.includes('scrollIntoView') || b.includes('scrollToBottom(')),
      'kart geldiğinde kaydırma yapan effect yok').toBe(true)
    expect(gateEffektleri.some(b => b.includes('setIsChatOpen(true)')),
      'kart geldiğinde sohbet panelini açan effect yok').toBe(true)
  })

  it('ayrıştırıcı yorumla kandırılamaz — tripwire\'ın kendi kanıtı', () => {
    // Eski sayaç bu girdide bloğu `// }` üzerinde kapatıyor ve `provider`
    // satırını hiç görmüyordu. Gerçek ayrıştırıcı ikisini de doğru okuyor.
    const kaynak = `const x = useMCPApproval({\n  API, // }\n  provider: 'subscription',\n})`
    const sf = ts.createSourceFile('t.tsx', kaynak, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let bulunan: ts.CallExpression | null = null
    const visit = (n: ts.Node): void => {
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'useMCPApproval') bulunan = n
      ts.forEachChild(n, visit)
    }
    visit(sf)
    const strings: string[] = []
    const gez = (n: ts.Node): void => {
      if (ts.isStringLiteral(n)) strings.push(n.text)
      ts.forEachChild(n, gez)
    }
    gez((bulunan as unknown as ts.CallExpression).arguments[0])
    expect(strings).toContain('subscription')
  })
})
