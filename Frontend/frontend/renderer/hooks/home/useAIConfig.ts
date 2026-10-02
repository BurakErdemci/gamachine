import { useState, useCallback, useEffect, useMemo, useRef, type SetStateAction } from 'react';
import axios from 'axios';
import { AIConfig, AvailableModels, ProviderReady, UserData } from '../../components/home/types';
import { apiHataMesaji } from '../../lib/apiError';
import { cevir } from '../../lib/i18n';
import { backendWorkspacePath } from '../../lib/backendWorkspacePath';
import { shortModelId } from '../../lib/modelText';

/**
 * Backend'in `GET /mcp/unity/status` sözleşmesi (`unity_mcp_manager.get_status`).
 *
 * `blocked` = 8080'i dinleyen biri var ama BİZ DEĞİL — kimlik testi (kimliksiz
 * `POST /mcp` → 401 + kendi izlerimiz) yabancı olduğunu söyledi. `off`tan ayrı
 * bir durum olması şart: `off` bir "aç" davetidir, `blocked` ise kullanıcıdan
 * BAŞKA bir eylem ister (çakışan sunucuyu kapatmak) ve toggle'a basmak
 * sunucumuzu başlatmıyor.
 *
 * `unknown` = son yoklama BAŞARISIZ oldu, durum artık bilinmiyor (bulgu I-2,
 * 30 Tem 2026). Üçünden de ayrı bir durum olması şart:
 *
 *   • `connected` DEĞİL — çünkü ölçüm yok. Eski davranış son başarılı ölçümü
 *     korumaktı ve ölçüldü: backend çöktükten sonra bile gösterge SÜRESİZ
 *     yeşil kalıyordu. K1/c'nin bütün varlık sebebi yabancı sunucu tespiti;
 *     tespit sonucu kullanıcıya ulaşmıyorsa yapılan azaltma etkisiz.
 *   • `off` DEĞİL — o bir "aç" daveti ve geçici bir ağ hatasında kullanıcıyı
 *     sunucusu kapanmış sanmaya iter. Eski `catch` yorumu bu yüzden durumu
 *     `off` yapmıyordu ve o kısım HAKLIYDI; eksik olan üçüncü seçenekti.
 *   • `blocked` DEĞİL — o, kimlik testinin YABANCI dediği ölçülmüş bir sonuç.
 */
/**
 * Tek kaynak: tip bu DİZİDEN türüyor, tersi değil.
 *
 * Sebebi bulgu I-1: durum listesi yalnız bir TİP olarak yazılmıştı ve tipler
 * çalışma anında yok oluyor. Aşağıdaki `unityMcpStatusOku` çalışma anında
 * doğrulama yapabilsin diye listenin bir DEĞER olarak da var olması gerekiyor;
 * ikisini elle ayrı tutmak, aynı bilginin iki kopyası demekti ve bu depoda
 * "birbiriyle uyuşması gereken iki yer" ölçülmüş bir arıza sınıfı.
 */
export const UNITY_MCP_DURUMLARI = [
  'off', 'blocked', 'starting', 'running', 'connected', 'unknown',
] as const;

export type UnityMCPStatus = typeof UNITY_MCP_DURUMLARI[number];

/**
 * Backend'den gelen ham `status` alanını birliğin İÇİNE zorlar.
 *
 * ⚠️ Bu bir tip cast'inin yerini alıyor (bulgu I-1). Eski satır
 * `res.data.status as UnityMCPStatus` idi ve `as` çalışma anında HİÇBİR ŞEY
 * yapmıyor: birlik dışı bir değer (backend'in eklediği yeni bir durum, bozuk
 * bir yanıt, araya giren bir vekil) doğrudan state'e giriyordu. İki tüketici de
 * durumu `Record<UnityMCPStatus, …>` sözlüğünde arıyor (`TONE[status]`,
 * `UNITY_STATUS_CONFIG[status]`) → `undefined` → `.btn`/`.border` okuması →
 * render sırasında TypeError → ErrorBoundary yoksa tüm ağaç unmount, BEYAZ EKRAN.
 *
 * `Record<...>` koruması yeterli sanılmıştı ve bu ölçülerek yanlışlandı:
 * `Record` yalnız tip DOĞRUYSA koruyor, cast'in altından geçen değer için
 * hiçbir şey yapmıyor (aynı tespit `UnityMcpToggle.tsx` başlığında da yazılı).
 *
 * Bilinmeyen değer `unknown`'a düşüyor, `off`a DEĞİL: `off` bir "aç" davetidir
 * ve tanımadığımız bir yanıtı davete çevirmek, ölçmediğimiz bir şeyi iddia
 * etmek olurdu — I-2'nin dersi.
 */
export function unityMcpStatusOku(ham: unknown): UnityMCPStatus {
  if (typeof ham === 'string' && (UNITY_MCP_DURUMLARI as readonly string[]).includes(ham)) {
    return ham as UnityMCPStatus;
  }
  // Sessiz düşmek, backend'in sözleşmeyi bozduğunu gizlerdi. Kullanıcıya toast
  // atmıyoruz (yoklama 8 saniyede bir koşuyor, ekranı doldururdu); geliştirici
  // konsolu bu bilginin doğru yeri.
  console.warn('[unityMCP] tanınmayan durum değeri, `unknown` sayıldı:', ham);
  return 'unknown';
}

/**
 * Model kataloğu state'i + SON ÇEKİMİN BAŞARISI.
 *
 * Bayrak neden burada, `types.ts`'teki `AvailableModels`'ta değil: `AvailableModels`
 * backend'in `/available-models` GÖVDESİNİN şekli — sunucu böyle bir alan
 * göndermiyor. Bu, isteğin kendisi hakkında bir istemci ölçümü; sunucu
 * sözleşmesine karıştırmak, olmayan bir alanı varmış gibi gösterirdi.
 *
 * Ayrı bir prop yerine state'in İÇİNDE olmasının sebebi ölçülmüş: `availableModels`
 * zaten dropdown'a ve ayarlara akıyor. Yeni bir prop, her tüketicide ayrı ayrı
 * bağlanmayı gerektirirdi ve bu depoda kayıtlı arıza sınıfı tam olarak bu —
 * bir yolda bağlanan, öbür yolda unutulan sinyal.
 */
export type AvailableModelsState = AvailableModels & {
  /** Son `/available-models` çağrısı başarısız oldu; listeler BOŞ değil, BİLİNMİYOR. */
  catalog_error?: boolean;
};

export const useAIConfig = (API: string, user: UserData | null, showToast: (msg: string, type: any) => void, workspacePath?: string) => {
  const [aiConfig, setAiConfigState] = useState<AIConfig>({
    provider_type: 'subscription', api_key: '', model_name: 'claude-sonnet-4-6', thinking_level: 'medium'
  });
  const aiConfigRef = useRef(aiConfig);
  aiConfigRef.current = aiConfig;
  // Per-chat model: `aiConfig` shows the model of the chat on screen (`chatIdRef`;
  // null = no chat, i.e. the default for a new one). Every load and every local
  // edit takes a number, and a load answers only if it is still the newest, so
  // a slow read for the chat just left, or one that lands after a pick, cannot
  // put another model on screen.
  const chatIdRef = useRef<number | null>(null);
  const modelSeqRef = useRef(0);
  const setAiConfig = useCallback((cfg: SetStateAction<AIConfig>) => {
    modelSeqRef.current += 1;
    setAiConfigState(cfg);
  }, []);
  const [availableModels, setAvailableModels] = useState<AvailableModelsState>({ local: [], cloud: [], subscription: [] });
  const [providersWithKeys, setProvidersWithKeys] = useState<string[]>([]);
  // Sohbet kapısı. Tek doğruluk kaynağı backend'de (`/provider-ready`): model →
  // CLI ailesi eşlemesi orada yaşıyor ve burada ikinci bir kopyasını tutmak
  // ikisini zamanla ayrıştırırdı.
  const [providerReady, setProviderReady] = useState<ProviderReady | null>(null);
  const [modelOrToggles, setModelOrToggles] = useState<Record<string, boolean>>({});
  const [showSettings, setShowSettings] = useState(false);
  const [isModelDropdownOpen, setIsModelDropdownOpen] = useState(false);

  // Unity MCP toggle
  const [unityMcpStatus, setUnityMcpStatus] = useState<UnityMCPStatus>('off');
  const [unityMcpToggling, setUnityMcpToggling] = useState(false);
  const [unityMcpError, setUnityMcpError] = useState<string | null>(null);
  // `blocked` sebebi. `unityMcpError` GEÇİCİ (bir toggle denemesinin sonucu, 6 sn
  // sonra siliniyor); bu KALICI, çünkü durumun kendisiyle yaşıyor: port
  // başkasındayken sebep ekranda durmalı, yoksa kullanıcı 6 saniye sonra elinde
  // yalnız gri bir düğmeyle kalıyor.
  const [unityMcpReason, setUnityMcpReason] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Yoklama kuşağı sayacı + montaj bayrağı; ikisi de `fetchUnityMcpStatus`'ta
  // kullanılıyor, gerekçe orada.
  const fetchNesilRef = useRef(0);
  const mountedRef = useRef(true);
  const startingUntilRef = useRef<number>(0); // Toggle ON'dan itibaren 30s boyunca 'off' yanıtını yoksay
  const errorClearRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const fetchUnityMcpStatus = useCallback(async () => {
    if (!API) return;
    // Yoklama kuşağı. İki ölçülmüş arızayı birden kapatıyor (doğrulama turu,
    // 30 Tem 2026):
    //
    //   1. ESKİ SONUÇ YENİYİ EZİYOR — iki yoklama uçuştayken önce başlayan
    //      sonra dönebiliyor ve bayat durumu geri yazıyor.
    //   2. TEMİZLİKTEN SONRA INTERVAL DİRİLİYOR — `useEffect` cleanup'ı
    //      `stopPolling()` çağırıyor ama uçuştaki istek iptal edilmiyor; o
    //      çözüldüğünde `catch`/`then` dalları YENİ bir `setInterval` kuruyor
    //      ve yoklama unmount'tan sonra sonsuza kadar sürüyordu.
    //
    // İkinci arızayı Faz 4 açtı: `catch` dalı eskiden yalnız interval
    // kuruyordu, şimdi `setState` de yapıyor — yani yaşayan bir sızıntıydı ve
    // düzeltme onu görünür hale getirdi.
    const nesil = ++fetchNesilRef.current;
    const guncelMi = () => nesil === fetchNesilRef.current && mountedRef.current;
    try {
      const res = await axios.get(`${API}/mcp/unity/status`);
      if (!guncelMi()) return;
      const status = unityMcpStatusOku(res.data?.status);
      const reason = typeof res.data?.reason === 'string' ? res.data.reason : null;

      // Toggle ON sonrası 30s içinde 'off' gelirse yoksay — sunucu henüz başlıyor olabilir.
      // `blocked` bu yutmanın DIŞINDA bırakıldı: o bir gecikme değil, kullanıcının
      // müdahalesini bekleyen bir çakışma; 30 sn susmak yalnız teşhisi geciktirir.
      if (status === 'off' && Date.now() < startingUntilRef.current) return;

      setUnityMcpStatus(status);
      // Sebep backend'de yalnız `blocked` dalında dolu. Başka durumda taşımak
      // bayat bir uyarıyı ekranda bırakırdı — kullanıcı çakışan sunucuyu
      // kapattıktan sonra bile "port başkasında" yazmaya devam ederdi.
      setUnityMcpReason(status === 'blocked' ? reason : null);
      if (status === 'connected') {
        stopPolling();
        pollRef.current = setInterval(fetchUnityMcpStatus, 15000);
      } else if (status === 'off' || status === 'blocked') {
        // Kapalı ama yine de 8s'de bir kontrol et — Unity kendi başlatmış olabilir.
        // `blocked` aynı ritimde: kullanıcı çakışan sunucuyu kapatınca durum
        // kendi kendine toparlanmalı, bir düğmeye basmaya gerek kalmadan.
        // ⚠️ Bu satırın `blocked` kısmı TEST EDİLMEDİ (ölçüldü 2026-07-30:
        // mutasyon kaçtı). Tek davranışsal farkı `connected` → `blocked`
        // geçişinde yoklamanın 15 sn yerine 8 sn olması, yani ~7 sn daha hızlı
        // toparlanma; sürmesi fake timer + interval sayacı ister ve kullanıcı
        // farkı görmez. Bilinçli boşluk, bulunmamış boşluk değil.
        stopPolling();
        pollRef.current = setInterval(fetchUnityMcpStatus, 8000);
      }
      // 'starting' veya 'running' → mevcut hızlı interval devam eder
    } catch {
      if (!guncelMi()) return;
      // Durum artık BİLİNMİYOR. Eskiden burada yalnız yoklama aralığı
      // değişiyordu ve son başarılı ölçüm olduğu gibi kalıyordu — ölçüldü
      // (bulgu I-2): backend çöktükten sonra bile gösterge süresiz `connected`
      // gösteriyordu. Yalan söyleyen bir gösterge, göstergesizlikten kötü.
      //
      // `off` yapılmıyor: o bir "aç" daveti ve geçici bir ağ hatasında
      // kullanıcıyı sunucusu kapanmış sanmaya iter. Eski yorumun bu kısmı
      // haklıydı; eksik olan "bilinmiyor" seçeneğiydi.
      setUnityMcpStatus('unknown');
      setUnityMcpReason(null);
      stopPolling();
      pollRef.current = setInterval(fetchUnityMcpStatus, 5000);
    }
  }, [API, stopPolling]);

  const toggleUnityMcp = useCallback(async (): Promise<boolean> => {
    if (!API || unityMcpToggling || unityMcpStatus === 'starting') return false;
    setUnityMcpToggling(true);
    // `blocked` de bir AÇMA denemesidir: kullanıcı çakışan sunucuyu kapattıysa
    // tek yolu bu düğme. Eskiden koşul `=== 'off'` olduğu için blocked'da
    // KAPATMA isteği gidiyordu; backend yabancı süreci öldürmediği için
    // sessizce 200 {"status":"stopped"} dönüyordu — yani kullanıcı butona
    // basıyor, hiçbir şey olmuyor ve bir hata bile görünmüyordu.
    const turningOn = unityMcpStatus === 'off' || unityMcpStatus === 'blocked';
    // Toggle da yoklama kuşağının İÇİNDE olmalı (2. doğrulama turu bulgusu).
    // İlk düzeltme yalnız `fetchUnityMcpStatus`'ı korumuştu; bu yol `await`
    // sonrası hem `setState` yapıyor hem `setInterval` kuruyordu, yani unmount
    // sırasında POST uçuştaysa temizlikten sonra yoklama yeniden doğuyordu.
    // Ayrıca kuşağı burada İLERLETMEK şart: toggle'dan ÖNCE başlamış bir
    // durum sorgusu, toggle'ın yazdığı `starting`/`off`'u ezebiliyordu.
    const nesil = ++fetchNesilRef.current;
    const guncelMi = () => nesil === fetchNesilRef.current && mountedRef.current;
    try {
      // Backend'in ADRESLEYEBILECEGI yol gonderilir. Docker modunda bu
      // konteynerdeki mount; ana makinenin yolu orada yok ve kurulum
      // `Packages/manifest.json`'i o yola gore cozuyor (denetim, 31 Agu 2026:
      // iki `/save-workspace` cagrisi cevrildi ama bu ucuncusu atlanmisti).
      const mcpWorkspace = workspacePath ? await backendWorkspacePath(workspacePath) : null;
      // Iki ayri `null` var ve karistirilmamalari lazim: acik klasor YOK (once
      // bir proje sec) ile acik klasor VAR ama backend onu adlandiramiyor
      // (Docker'da mount disinda). Ikincisinde sunucu yine kalkiyor ama paket
      // ve autoconnect yazimlari atlaniyor — yani Unity kurulumu sessizce
      // yarim kaliyor. Bu depoda en pahaliya mal olan bicim tam olarak bu, o
      // yuzden kullaniciya soyleniyor.
      if (turningOn && workspacePath && mcpWorkspace === null) {
        showToast(cevir('workspace.outsideDockerMount'), 'warning');
      }
      await axios.post(`${API}/mcp/unity/toggle`, { enabled: turningOn, workspace_path: mcpWorkspace });
      if (!guncelMi()) return false;
      if (turningOn) {
        setUnityMcpStatus('starting');
        startingUntilRef.current = Date.now() + 30000; // 30s boyunca 'off' yanıtını yoksay
        stopPolling();
        pollRef.current = setInterval(fetchUnityMcpStatus, 3000);
      } else {
        stopPolling();
        startingUntilRef.current = 0;
        setUnityMcpStatus('off');
        pollRef.current = setInterval(fetchUnityMcpStatus, 8000);
      }
      return true;
    } catch (err: any) {
      if (!guncelMi()) return false;
      // `detail` artık HER durum kodunda okunuyor. 500'ün gövdesi portu tutan
      // sürecin ADINI taşıyor (`unity_mcp_manager._blocked_reason`) ve eskiden
      // sabit bir "toggle başarısız" metniyle eziliyordu: sebep üretiliyor ama
      // kullanıcıya hiç ulaşmıyordu.
      // Tip kontrolü kozmetik değil, çökme kapısı — gerekçesi ve korunan
      // çökme `apiHataMesaji`'nin gövdesinde. Buradan çıkarılmasının sebebi
      // bulgu I-3: aynı koruma `ModelSelector`'da YOKTU, çünkü kopyalanmıştı.
      const msg = apiHataMesaji(
        err,
        err?.response?.status === 409
          ? cevir('unity.editorClosed')
          : cevir('unity.toggleFailed'),
      );
      showToast(msg, 'error');
      setUnityMcpError(msg);
      // 6 saniye sonra uyarıyı temizle
      if (errorClearRef.current) clearTimeout(errorClearRef.current);
      errorClearRef.current = setTimeout(() => setUnityMcpError(null), 6000);
      // Durumu TAHMİN etmiyoruz, ÖLÇÜYORUZ. Eskiden koşulsuz 'off' yazılıyordu;
      // port başkasındayken bu yanlış bilgi: kullanıcı gri "kapalı" görüyor,
      // aynı düğmeye basıyor ve sebebi hiç öğrenmiyor.
      await fetchUnityMcpStatus();
      return false;
    } finally {
      setUnityMcpToggling(false);
    }
  }, [API, unityMcpStatus, unityMcpToggling, fetchUnityMcpStatus, stopPolling, showToast, workspacePath]);

  // Başlangıçta sorgula ve sürekli kontrol et
  useEffect(() => {
    if (!API) return;
    mountedRef.current = true;
    fetchUnityMcpStatus();
    // 8s'de bir otomatik kontrol — Unity kendi başlatmış olabilir
    pollRef.current = setInterval(fetchUnityMcpStatus, 8000);
    return () => {
      // Kuşağı ilerletmek uçuştaki isteğin dönüşünü ETKİSİZ kılıyor: `stopPolling`
      // tek başına yetmiyordu, çünkü çözülen istek yeni bir interval kuruyordu.
      mountedRef.current = false;
      fetchNesilRef.current++;
      stopPolling();
    };
  }, [API]); // eslint-disable-line react-hooks/exhaustive-deps

  // Puts on screen the model of chat `convId`, or with no chat the default for
  // a new one. Read fresh on every activation: another window or the phone may
  // have changed the chat's model since this window last showed it. Only a pick
  // writes; showing a chat never changes any chat's model.
  const showChatModel = useCallback(async (userId: number, convId: number | null) => {
    if (!API) return;
    chatIdRef.current = convId;
    const seq = ++modelSeqRef.current;
    try {
      const res = convId == null
        ? await axios.get(`${API}/get-ai-config/${userId}`)
        : await axios.get(`${API}/conversations/${convId}/model`);
      if (seq !== modelSeqRef.current || !res.data) return;
      setAiConfigState({ ...res.data, api_key: '' });
    } catch (err) { console.error("Config hatası:", err); }
  }, [API]);

  const fetchAIConfig = useCallback(
    (userId: number) => showChatModel(userId, chatIdRef.current), [showChatModel]);

  const readySeqRef = useRef(0);
  const fetchProviderReady = useCallback(async (userId: number, refresh = false) => {
    if (!API) return;
    const seq = ++readySeqRef.current;
    // The gate judges the pair on screen (the chat's model), not the default.
    const { provider_type, model_name } = aiConfigRef.current;
    try {
      const res = await axios.get(`${API}/provider-ready/${userId}`, {
        params: { provider_type, model_name: model_name ?? '', ...(refresh ? { refresh: true } : {}) },
      });
      if (seq !== readySeqRef.current) return;
      setProviderReady(res.data ?? null);
    } catch {
      if (seq !== readySeqRef.current) return;
      // ⚠️ Ölçüm BAŞARISIZ olduğunda kapı AÇIK bırakılıyor (fail-open), ve bu
      // bilinçli: backend'e ulaşamamak "sağlayıcı yok" demek değil. Burada
      // fail-closed davranmak, çalışan bir kurulumu olan kullanıcıyı geçici bir
      // ağ/başlatma hatası yüzünden kilitlerdi — yani kapı, çözmek için var
      // olduğu problemi kendisi üretirdi. Backend gerçekten çökmüşse zaten ayrı
      // bir kurtarma ekranı devrede (`home.tsx`, backendDown dalı).
      setProviderReady(null);
    }
  }, [API]);

  // ⚠️ `user?.sessionToken` bağımlılıkta OLMAK ZORUNDA. Eskiden yalnız `[API]`
  // vardı ve fonksiyon ilk render'ın `user`'ına kilitleniyordu; o anda token
  // henüz `useAuth`'un başlangıç değeri olan `'local'` (IPC `app-token-get`
  // sonradan çözülüyor). Sonuç ÖLÇÜLDÜ: istek daima `X-Session-Token: local`
  // ile gidiyor, `_check_token` 401 veriyor, `catch` sessizce yutuyor ve
  // `availableModels` sonsuza kadar boş kalıyor.
  //
  // Kullanıcıya yansıması: BÜTÜN bulut/API modelleri listeden kayboluyor
  // (bölüm `cloudGroups.length > 0` ile gizleniyor) ve `dynamic` alanı olmayan
  // CLI grupları — Claude Code, Antigravity, Kimi — boş açılıyor. Kendi
  // `/cli-models/{cli}` ucundan besleneni (codex/copilot/cursor/opencode)
  // etkilenmiyordu; arızayı bu kadar kafa karıştırıcı yapan da o asimetriydi.
  //
  // Açık başlık `axios.defaults.headers.common`daki DOĞRU değeri de eziyor,
  // yani genel varsayılan burada kurtarıcı olamıyor.
  // `force`: sağlayıcı listelerinin 10 dakikalık önbelleğini atla. Kullanıcı
  // "yenile" derken tam olarak bayat cevabı istemiyor.
  const fetchAvailableModels = useCallback(async (force = false) => {
    if (!API) return;
    try {
      const res = await axios.get(`${API}/available-models`, {
        params: force ? { refresh: true } : undefined,
        headers: { 'X-Session-Token': user?.sessionToken ?? '' },
      });
      if (res.data) setAvailableModels({ ...res.data, catalog_error: false });
    } catch (err) {
      console.error("Modeller alınamadı:", err);
      // Konsol KULLANICI ARAYÜZÜ DEĞİL. Eski hâlde tek iz buydu ve sonuç
      // ölçüldü: `/available-models` düşünce dropdown bulut bölümünü sessizce
      // gizliyordu (`cloudGroups.length > 0`), yani geçici bir katalog arızası
      // "bütün modeller kayboldu" gibi görünüyordu.
      //
      // Eldeki liste SİLİNMİYOR: bayat bir liste, hiç liste olmamasından iyi —
      // yeter ki bayat olduğu yazsın. Bayrak dropdown'daki uyarı+yeniden dene
      // satırını sürüyor; toast ise açılış anındaki arıza için tek kanal,
      // çünkü o sırada dropdown kapalı ve uyarı satırı görünmüyor.
      setAvailableModels(prev => ({ ...prev, catalog_error: true }));
      showToast(cevir('models.catalogFailed'), 'error');
    }
  }, [API, user?.sessionToken, showToast]);

  const fetchProvidersWithKeys = useCallback(async (userId: number) => {
    if (!API) return;
    try {
      const res = await axios.get(`${API}/api-keys/${userId}`);
      if (res.data?.providers_with_keys) setProvidersWithKeys(res.data.providers_with_keys);
    } catch (err) { console.error("API keys hatası:", err); }
  }, [API]);

  const saveAIConfig = useCallback(async () => {
    if (!user || !API) return;
    try {
      // Saved from Settings is a pick like the dropdown's: the chat on screen
      // takes it as well as the default for new chats.
      const configToSave = {
        ...aiConfig, user_id: user.id,
        ...(chatIdRef.current != null ? { conversation_id: chatIdRef.current } : {}),
      };
      const isCloud = !['ollama', 'kb'].includes(configToSave.provider_type);

      if (!isCloud) configToSave.api_key = '';

      if (configToSave.api_key && isCloud) {
        await axios.post(`${API}/api-keys/save`, {
          user_id: user.id,
          provider_type: configToSave.provider_type,
          api_key: configToSave.api_key
        });
      }

      if (isCloud && !configToSave.api_key && !providersWithKeys.includes(configToSave.provider_type)) {
        showToast(cevir('settings.apiKeyMissingFor', { saglayici: configToSave.provider_type }), 'warning');
        return;
      }

      await axios.post(`${API}/save-ai-config`, configToSave);
      setAiConfig({ ...aiConfig, api_key: '' });
      await fetchProvidersWithKeys(user.id);
      showToast(cevir('settings.saved'), 'success');
      setShowSettings(false);
    } catch (err) { showToast(cevir('settings.saveFailed'), 'error'); }
  }, [API, aiConfig, fetchProvidersWithKeys, providersWithKeys, showToast, user]);

  const deleteApiKey = useCallback(async (provider: string): Promise<boolean> => {
    if (!user || !API) return false;
    try {
      await axios.delete(`${API}/api-keys/${user.id}/${provider}`);
      await fetchProvidersWithKeys(user.id);
      setAiConfig(prev => ({ ...prev, api_key: '' }));
      return true;
    } catch (err) {
      showToast(cevir('settings.keyDeleteError'), 'error');
      return false;
    }
  }, [API, fetchProvidersWithKeys, showToast, user]);

  // ── Live-apply writers for the settings screen (round 11) ─────────────────
  // The old modal collected a provider, a key and a model and wrote them with one
  // Save. The screen applies each of them the moment it is committed, so each has
  // its own writer; every one reports success so the screen can flash "Saved".

  // The default for a new chat (`/get-ai-config`), which the settings page shows
  // even while a chat with another model is on screen.
  const [defaultConfig, setDefaultConfig] = useState<{ provider_type: string; model_name: string } | null>(null);
  const fetchDefaultConfig = useCallback(async () => {
    if (!API || !user) return;
    try {
      const res = await axios.get(`${API}/get-ai-config/${user.id}`);
      if (res?.data) setDefaultConfig({ provider_type: res.data.provider_type, model_name: res.data.model_name });
    } catch (err) { console.error("Config hatası:", err); }
  }, [API, user]);

  /** One provider's API key, written on its own (no model change). */
  const saveApiKey = useCallback(async (provider: string, key: string): Promise<boolean> => {
    if (!user || !API || !key.trim()) return false;
    try {
      await axios.post(`${API}/api-keys/save`, { user_id: user.id, provider_type: provider, api_key: key.trim() });
      await fetchProvidersWithKeys(user.id);
      return true;
    } catch (err: any) {
      showToast(apiHataMesaji(err, cevir('settings.saveFailed')), 'error');
      return false;
    }
  }, [API, fetchProvidersWithKeys, showToast, user]);

  /**
   * "Varsayılan model": the default for new chats only. Written without a
   * conversation id, so the chat on screen keeps its own model; with no chat on
   * screen the screen shows that default, so it follows.
   */
  const saveDefaultModel = useCallback(async (provider_type: string, model_name: string): Promise<boolean> => {
    if (!user || !API) return false;
    // Same api_key rule as the menu's picks: never a stale key that could overwrite a stored one.
    const cfg = { provider_type, model_name, api_key: provider_type === 'subscription' ? 'CLI_SESSION' : '' };
    try {
      await axios.post(`${API}/save-ai-config`, { ...aiConfigRef.current, ...cfg, user_id: user.id });
      setDefaultConfig({ provider_type, model_name });
      if (chatIdRef.current == null) setAiConfig(prev => ({ ...prev, ...cfg, api_key: '' }));
      return true;
    } catch (err: any) {
      showToast(apiHataMesaji(err, cevir('settings.saveFailed')), 'error');
      return false;
    }
  }, [API, setAiConfig, showToast, user]);

  /**
   * "Özel model kimliği": a model id typed by hand, sent to the provider on screen.
   * It is the old modal's model-name field + Save: the chat on screen takes it as
   * well as the default, and a cloud provider without a stored key is refused.
   */
  const applyCustomModel = useCallback(async (modelName: string): Promise<boolean> => {
    const name = modelName.trim();
    if (!user || !API || !name) return false;
    const current = aiConfigRef.current;
    const isCloud = !['ollama', 'kb', 'subscription'].includes(current.provider_type);
    if (isCloud && !providersWithKeys.includes(current.provider_type)) {
      showToast(cevir('settings.apiKeyMissingFor', { saglayici: current.provider_type }), 'warning');
      return false;
    }
    const cfg = { ...current, model_name: name, api_key: current.provider_type === 'subscription' ? 'CLI_SESSION' : '' };
    try {
      await axios.post(`${API}/save-ai-config`, {
        ...cfg, user_id: user.id,
        ...(chatIdRef.current != null ? { conversation_id: chatIdRef.current } : {}),
      });
      setAiConfig({ ...cfg, api_key: '' });
      setDefaultConfig({ provider_type: cfg.provider_type, model_name: name });
      return true;
    } catch (err: any) {
      showToast(apiHataMesaji(err, cevir('settings.saveFailed')), 'error');
      return false;
    }
  }, [API, providersWithKeys, setAiConfig, showToast, user]);

  const effectiveProvider = useMemo(() => aiConfig.provider_type, [aiConfig.provider_type]);

  const displayModelName = useMemo(() => {
    if (!aiConfig.model_name) return cevir('models.select');
    const allModels = [...availableModels.cloud, ...(availableModels.subscription || [])];
    const matches = (m: any) => m.id === aiConfig.model_name || m.openrouter_id === aiConfig.model_name;
    // The same id can sit in both lists: `claude-sonnet-5-5` is a Claude Code
    // row and also an Anthropic API row named after OpenRouter ("Anthropic:
    // Claude Sonnet 5.5"). Take the row of the active provider first, or the
    // chip shows the API name for a CLI pick (owner report, 2 Oct 2026).
    const found = allModels.find(m => (m as any).provider === aiConfig.provider_type && matches(m))
      || allModels.find(matches);
    if (found) return found.name;
    // Dinamik CLI modelleri (cursor/opencode) statik listede yok → prefix'i soy.
    return shortModelId(aiConfig.model_name);
  }, [aiConfig, availableModels]);

  return {
    aiConfig,
    setAiConfig,
    availableModels,
    providersWithKeys,
    modelOrToggles,
    setModelOrToggles,
    showSettings,
    setShowSettings,
    isModelDropdownOpen,
    setIsModelDropdownOpen,
    fetchAIConfig,
    showChatModel,
    fetchAvailableModels,
    fetchProvidersWithKeys,
    providerReady,
    fetchProviderReady,
    saveAIConfig,
    deleteApiKey,
    defaultConfig,
    fetchDefaultConfig,
    saveApiKey,
    saveDefaultModel,
    applyCustomModel,
    effectiveProvider,
    displayModelName,
    unityMcpStatus,
    unityMcpToggling,
    unityMcpError,
    unityMcpReason,
    toggleUnityMcp,
    // Durumu dışarıdan tazelemek için. İki tüketicisi var ve ikisi de aynı
    // soruyu soruyor: "şu an gerçekten ne durumda" — hata sonrası ölçüm, ve
    // testlerin "kullanıcı çakışan sunucuyu kapattı" senaryosu.
    refreshUnityMcpStatus: fetchUnityMcpStatus,
  };
};
