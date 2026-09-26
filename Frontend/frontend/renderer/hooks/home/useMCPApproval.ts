/**
 * useMCPApproval — MCP server'dan gelen onay isteklerini dinler ve
 * mevcut approval UI'larına (DiffViewer, FileCreationApproval,
 * FileDeleteApproval, CommandApproval) yönlendirir.
 *
 * Polling: /mcp-pending endpoint'ini 1 saniyede bir kontrol eder.
 * Kullanıcı onaylarsa/reddederse /mcp-approval-respond/{gate_id} çağrılır.
 *
 * ⚠️ Bu yol SSE DEĞİL, polling — backend tarafında da öyle. Bunu yazmaya değer
 * çünkü tersini iddia eden yorumlar vardı ve bir tasarım tartışmasında "zaten
 * SSE var" diye okundu. SSE'nin neden seçilmediği polling aralığının yanında.
 *
 * ⚠️ AYNI ANDA TEK KART. Bekleyen istek kuyruğu backend'in `_mcp_pending`
 * sözlüğü; bu hook oradan bir seferde bir tane alır ve karar verilene kadar
 * yenisini almaz. Gerekçesi ölçülmüş bir arıza (2026-07-29 denetimi): eskiden
 * her yoklamada bekleyenlerin HEPSİ işleniyordu, hepsi aynı tek kart slotuna
 * yazıyordu ve sonuncusu öncekilerin üstüne biniyordu. Üstü çizilen istek
 * "görüldü" işaretlendiği için bir daha hiç gösterilmiyor, köprüde 180 sn
 * bekleyip reddediliyordu.
 *
 * Routing (Phase 3 slice 2): each entry names its owner chat. The single slot
 * above only ever holds a request of the chat on screen; another chat's
 * request waits in the backend (its sidebar row says so) until that chat is
 * opened. A request with no known owner is not a card in any chat: it goes to
 * the tray (`McpUnknownTray`), which lists all of them at once.
 */
import { useEffect, useRef, useCallback, useState } from 'react';
import axios from 'axios';
import { PendingFile } from '../../components/home/FileCreationApproval';
import { cevir } from '../../lib/i18n';
import { backendWorkspacePath } from '../../lib/backendWorkspacePath';

/**
 * Ekranda karar bekleyen MCP isteği. Kartı çizen taraf gate kimliğini ve
 * isteğin GELDİĞİ workspace'i buradan okur.
 */
export interface McpActiveGate {
  gateId: string;
  tool: string;
  /**
   * İsteği açan tarafın çalışma dizini (`_mcp_pending[...].workspace_path`).
   * Boş olabilir: köprü bu alanı göndermek zorunda değil ve eski sürümler
   * göndermiyordu. Boşluk "eşleşiyor" diye okunmamalı — bkz `workspaceMismatch`.
   */
  workspacePath: string;
  /**
   * The chat that owns the request (`conversation_id`). `undefined` means the
   * backend predates ownership and sent no such field: the card follows the
   * chat on screen, as it always did. `null` is the backend saying it does
   * not know; such a request never gets here, it goes to the tray.
   */
  conversationId?: number | null;
}

/** A request whose source chat the backend could not name (tray entry). */
export interface McpTrayGate {
  gateId: string;
  tool: string;
  params: any;
  workspacePath: string;
}

/**
 * Who owns a `/mcp-pending` entry: a chat id, `null` (unknown), or
 * `undefined` (the field is absent: a backend from before ownership).
 * Anything malformed counts as unknown, so it lands in the tray where it is
 * still decidable, never in a chat that did not ask for it.
 */
export const gateOwner = (req: any): number | null | undefined => {
  if (!req || typeof req !== 'object' || !('conversation_id' in req)) return undefined;
  const id = req.conversation_id;
  return typeof id === 'number' && Number.isInteger(id) && id > 0 ? id : null;
};

/** May a card with this owner be drawn in the chat on screen? */
const belongsOnScreen = (owner: number | null | undefined, screenConvId: number | null | undefined) =>
  owner === undefined || (owner !== null && owner === screenConvId);

interface MCPApprovalHookParams {
  API: string;
  /**
   * "Backend hazır ve oturum token'ı axios'a kuruldu" demek — SAĞLAYICI DEĞİL.
   *
   * Eskiden `effectiveProvider === 'subscription'` bağlanıyordu ve yanına
   * `loading: chat.loading` koşulu vardı. İkisi de 2026-07-29'da kaldırıldı:
   * onay kartı üreten köprü (`approval_bridge.py`) sağlayıcıyı hiç bilmiyor ve
   * 9 sağlayıcının 9'u da aynı uca gidiyor, yani kartı sağlayıcıya bağlamak
   * kartı 8 yolda yok ediyordu. `loading` de yanlıştı: CLI sağlayıcıları sohbet
   * "idle" görünürken araç çağırabiliyor.
   *
   * Token kurulmadan yoklamak anlamsız: `/mcp-pending` `X-Session-Token`
   * istiyor (`auth_utils._check_token`) ve token `useAuth` içinde IPC'den
   * geldikten sonra `axios.defaults`'a yazılıyor. Öncesinde her saniye sessizce
   * yutulan bir 401 üretilirdi.
   */
  enabled: boolean;
  /**
   * ÜRÜNDE açık olan workspace. Gate'in kendi workspace'iyle karşılaştırılıp
   * kullanıcıya gösterilir; kartı GİZLEMEK için kullanılmaz (karar 2026-07-29,
   * kullanıcı: eşleşmeyen kart gösterilsin ama hangi projeye ait olduğu yazsın).
   *
   * Gizlememenin gerekçesi: unityMCP'yi doğrudan başka bir istemciye (Cursor
   * vb.) bağlamış kullanıcı üründe o projeyi açmamış olabilir; kartı gizlemek
   * onu 180 sn'lik sessiz bir redde kilitlerdi. Bedeli açıkça kabul edildi:
   * koruma, kullanıcının banner'ı OKUMASINA bağlı.
   */
  workspacePath: string | null;
  setPendingGenFiles: (val: { files: PendingFile[]; messageId: number } | null) => void;
  setPendingDelete: (val: { path: string; messageId: number } | null) => void;
  setPendingCommand: (val: { command: string; gateId: string; messageId: number; kind?: 'shell' | 'unity' } | null) => void;
  setPendingFix: (val: any) => void;
  // Kararın backend'e ULAŞMADIĞINI kullanıcıya bildirmek için. Opsiyonel:
  // hook'u test/başka bağlamda toast'sız kurmak mümkün kalsın.
  showToast?: (msg: string, type: any) => void;
  /** The chat on screen. Only its own requests are drawn in the chat panel. */
  screenConvId?: number | null;
  /**
   * Every poll reports which chats own a pending request, so a chat that is
   * off screen can say "awaiting approval" in the sidebar.
   */
  onOwnersChange?: (gatesByConv: Record<number, string[]>) => void;
}

/**
 * MCP onay isteği için sanal mesaj ID'si (gerçek chat mesajına bağlı değil).
 *
 * DIŞARI AÇIK olmasının sebebi ölçülmüş bir arıza sınıfı: ChatPanel bu değeri
 * `-999` diye ELLE tekrar ediyordu ve `pendingFix` dalı hiç yazılmamıştı. Bu
 * depodaki arızaların ortak biçimi "birbiriyle uyuşması gereken iki yer
 * uyuşmuyor"; sabiti tek yerden okutmak o sınıfın bu örneğini kapatıyor.
 */
export const MCP_MSG_ID = -999;

/** Yoklama aralığı (ms). Gerekçesi ve ölçümü aşağıdaki useEffect'te. */
const POLL_INTERVAL_MS = 1000;

/**
 * Kaç ardışık başarısız yoklamadan sonra kullanıcı uyarılır.
 *
 * 1 değil, çünkü backend yeniden başlarken bir-iki yoklama düşer ve her
 * düşüşte toast basmak gürültüdür. Sonsuz da değil: yoklama artık koşulsuz
 * çalıştığı için kalıcı bir 401/403 (yanlış token) hiçbir iz bırakmadan
 * ürünün onay yolunu tamamen ölü hale getiriyordu — dış denetim bulgusu
 * `silent-approval-delivery-failure` (2026-07-29). 5 × 1 sn = 5 sn, yani
 * geçici kesinti sessiz kalıyor, kalıcı arıza görünür oluyor.
 */
const POLL_FAILURE_ALERT_AFTER = 5;

/** Unity araç çağrısını kartta okunacak tek bir metne indirger.
 *
 * Onay kartının işi kullanıcıya NE onayladığını göstermek; yalnız araç adını
 * göstermek (`manage_gameobject`) "neyi siliyorum" sorusunu cevapsız bırakır.
 * Parametreler bu yüzden yazılıyor ama KIRPILIYOR: `execute_code` bütün bir C#
 * bloğu taşıyabiliyor ve kırpılmamış hali kartı ekrandan taşırır — okunamayan
 * bir kart, okunmadan onaylanan bir karttır.
 */
/** Özet ÖZYİNELİYOR — ve sebebi bir denetim bulgusu (31 Tem 2026, med).
 *
 * İlk yazımı her üst düzey değeri TEK PARÇA serileştirip 200 karakterde
 * kesiyordu. `batch_execute`'ta bütün alt komutlar tek bir `commands` değeri
 * olduğu için, ilk komut uzun tutulduğunda ikinci sıradaki bir SİLME işlemi
 * kartta hiç görünmüyordu — ama onay bütün paketi yetkilendiriyordu. Yani
 * kullanıcı göremediği bir şeyi onaylıyordu ve kartın varlık sebebi ortadan
 * kalkıyordu.
 *
 * Kırpma artık YAPRAK başına. Satır sınırına ulaşılırsa gizlenen alan sayısı
 * kartta AÇIKÇA yazılıyor: sessiz kırpma, kırpmanın kendisinden tehlikeli.
 *
 * ⚠️ İlk düzeltmede DERİNLİK SINIRI (6) vardı ve o sınıfı geri açıyordu: iç içe
 * bir `batch_execute` 6. derinliğe ulaşınca alt ağaç yine tek parça kesiliyor,
 * üstelik uyarı bile basılmıyordu. Sunucu sınıflandırması iç içe paketleri 8
 * derinliğe kadar destekliyor, yani şekil erişilebilirdi (3. denetim turu,
 * 31 Tem 2026). Sınırı BÜYÜTMEK yamamak olurdu — kaldırıldı, yerine döngü
 * koruması kondu. Ders: bir kapatmanın içine konan "makul" sabit, kapattığı
 * sınıfın yeni bir örneğini üretebiliyor. */
const OZET_SATIR_SINIRI = 200;
/** Yaprak başına gösterilen karakter.
 *
 * 200 idi ve bir denetim bulgusu (31 Tem 2026) onu şöyle kırdı: uzun bir
 * `execute_code` gövdesinin YIKICI kısmı 200. karakterden sonraysa kartta hiç
 * görünmüyordu — kullanıcı zararsız görünen bir başlangıcı onaylıyordu. Kart
 * kaydırılabilir bir blok, yani asıl kısıt okunabilirlik değil dürüstlük.
 *
 * 4000, gerçek yüklerin neredeyse tamamını gösteriyor; aşıldığında GİZLENEN
 * KARAKTER SAYISI yazılıyor. Sessiz kırpma ile sayılı kırpma arasındaki fark,
 * kullanıcının "burada dahası var mı" sorusunu sorabilmesi. */
const OZET_DEGER_SINIRI = 4000;

export const unityOzeti = (tool: string, params: any): string => {
  const satirlar: string[] = [];
  let atlanan = 0;
  // Döngü koruması: MCP yükü JSON'dan geldiği için döngü İÇEREMEZ, ama bu
  // fonksiyon başka bir çağıran tarafından da kullanılabilir ve sonsuz
  // özyineleme kartı hiç çizdirmez — yani gizlemenin en sert biçimi olurdu.
  const gorulen = new WeakSet<object>();

  const yaz = (onek: string, deger: any, derinlik: number): void => {
    if (satirlar.length >= OZET_SATIR_SINIRI) { atlanan += 1; return; }
    const nesne = deger !== null && typeof deger === 'object';
    if (nesne) {
      if (gorulen.has(deger as object)) {
        satirlar.push(`${onek}: ${cevir('mcp.circularRef')}`);
        return;
      }
      gorulen.add(deger as object);
      const girdiler: Array<[string, any]> = Array.isArray(deger)
        ? deger.map((v, i) => [String(i), v])
        : Object.entries(deger);
      if (girdiler.length === 0) { satirlar.push(`${onek}: ${cevir('mcp.emptyValue')}`); return; }
      for (const [anahtar, alt] of girdiler) {
        yaz(onek ? `${onek}.${anahtar}` : anahtar, alt, derinlik + 1);
      }
      return;
    }
    const metin = typeof deger === 'string' ? deger : JSON.stringify(deger);
    const guvenli = metin === undefined ? 'undefined' : String(metin);
    const kisa = guvenli.length > OZET_DEGER_SINIRI
      ? `${guvenli.slice(0, OZET_DEGER_SINIRI)}… [+${cevir('mcp.charsHidden', { sayi: guvenli.length - OZET_DEGER_SINIRI })}]`
      : guvenli;
    satirlar.push(`${onek}: ${kisa}`);
  };

  const kok = params && typeof params === 'object' ? Object.entries(params) : [];
  // Hedef proje EN ÜSTTE. Eskiden `unity_instance` "yönlendirme detayı,
  // kullanıcının kararına girmiyor" gerekçesiyle FİLTRELENİYORDU — ve
  // doğrulama turu bu filtrenin, sunucu tarafındaki düzeltmeyi tam olarak
  // iptal ettiğini gösterdi: kapı hedefi parametrelere ekliyordu, burası onu
  // atıyordu, kart yine hangi projenin değişeceğini söylemiyordu.
  //
  // Gerekçe de yanlıştı: birden fazla Editor bağlıyken "hangi proje" sorusu
  // detay değil, kararın KENDİSİ. Yamanın iki yarısının birbirini iptal
  // etmesi, ikisini ayrı zamanlarda yazmanın bedeli.
  // GÜVENLİK KONTROLLERİ KAPALIYSA bunu EN ÜSTTE bağır. Parametre zaten
  // aşağıdaki döngüde `safety_checks: false` diye yazılıyordu, ama uzun bir
  // parametre listesinin ortasındaki küçük bir satır, kararın en önemli
  // parçasını taşıyamaz — kullanıcı kodu okur, bayrağı kaçırır.
  //
  // Koşul `=== false` DEĞİL, "true değilse". Fark bir uyarı için önemli:
  // `=== false` yazsaydık `"false"` dizesi ya da `0` gibi bozuk bir yük
  // uyarıyı SESSİZCE düşürürdü — yani uyarı tam da yükün güvenilmez olduğu
  // durumda kaybolurdu. Karar veren kod (kapının kendisi) `is True`/`=== true`
  // ile fail-CLOSED çalışır; GÖSTEREN kod fail-LOUD çalışmalı.
  const guvenlikBayragi = params && typeof params === 'object'
    ? (params as any).safety_checks
    : undefined;
  if (guvenlikBayragi !== undefined && guvenlikBayragi !== true) {
    satirlar.push(cevir('mcp.safetyOff'));
  }
  const hedef = params && typeof params === 'object' ? (params as any).unity_instance : undefined;
  if (typeof hedef === 'string' && hedef) {
    satirlar.push(`unity_instance: ${hedef}`);
  }
  for (const [anahtar, deger] of kok) {
    if (anahtar === 'unity_instance') continue;  // yukarıda zaten yazıldı
    yaz(anahtar, deger, 0);
  }
  if (atlanan > 0) {
    satirlar.push(cevir('mcp.fieldsHidden', { sayi: atlanan }));
  }
  return satirlar.length ? `${tool}\n${satirlar.join('\n')}` : tool;
};

/**
 * Gate'in workspace'i ile üründe açık workspace uyuşmuyor mu?
 *
 * Ayrı fonksiyon ve dışarı açık: kararı hem hook (uyarı üretmek için) hem de
 * kartı çizen bileşen (banner rengi için) veriyor; iki yerde ayrı ayrı yazılan
 * bir koşul bu depoda tekrar tekrar ayrıştı.
 *
 * Bilinmeyen durum `false` döner — "uyuşmuyor" DEĞİL. Ne gate'in workspace'i
 * boşken ne de üründe workspace açık değilken bir çelişki KANITLANMIŞ olmuyor;
 * bilinmeyeni "uyuşmazlık" saymak, gerçek uyuşmazlığı fark edilmez kılacak
 * kadar çok yanlış uyarı üretirdi. Bilinmezliği kullanıcıya ayrıca banner
 * söylüyor (workspace yazılı, "açık olan" satırı yoksa bilinmiyor demek).
 */
export const workspaceMismatch = (
  gateWorkspace: string | null | undefined,
  openWorkspace: string | null | undefined,
): boolean => {
  if (!gateWorkspace || !openWorkspace) return false;
  return gateWorkspace.replace(/\/+$/, '') !== openWorkspace.replace(/\/+$/, '');
};

export const useMCPApproval = ({
  API,
  enabled,
  workspacePath,
  setPendingGenFiles,
  setPendingDelete,
  setPendingCommand,
  setPendingFix,
  showToast,
  screenConvId,
  onOwnersChange,
}: MCPApprovalHookParams) => {
  const [activeGate, setActiveGate] = useState<McpActiveGate | null>(null);
  // Unknown-owner requests are not queued behind the single card slot: each
  // one is drawn in the tray with its own buttons, so several can wait at once.
  const [unknownGates, setUnknownGates] = useState<McpTrayGate[]>([]);
  // At least one poll has been answered. Requests in that first answer were
  // already waiting before this renderer existed (a reload, a restart), so the
  // desktop notifications treat them as known rather than as new.
  const [synced, setSynced] = useState(false);
  const screenConvRef = useRef(screenConvId);
  screenConvRef.current = screenConvId;
  const onOwnersChangeRef = useRef(onOwnersChange);
  onOwnersChangeRef.current = onOwnersChange;
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  /**
   * `activeGate`'in ref ikizi. State değil ref okunuyor çünkü `poll` bir
   * `useCallback` ve state'i bağımlılığına alsaydı her kart açılışında yeni
   * kimlik alıp effect'i yeniden kurardı (interval sıfırlanır, mount anındaki
   * `poll()` tekrar koşardı). Ref, kararın SENKRON okunmasını da garanti
   * ediyor: iki yoklama üst üste bindiğinde ikincisi birincinin açtığı kartı
   * görmek zorunda.
   */
  const activeGateRef = useRef<McpActiveGate | null>(null);
  /** Açık kartın state'ini geri alan fonksiyon. Bkz `dismissActive`. */
  const clearActiveCardRef = useRef<(() => void) | null>(null);
  const failStreakRef = useRef(0);
  const alertedRef = useRef(false);

  /**
   * Açık kartı kaldırır ve sırayı serbest bırakır.
   *
   * Neden kartın state'ini TEK bir kayıtlı fonksiyonla temizliyoruz da dördünü
   * birden `null`'lamıyoruz: `pendingFix`/`pendingGenFiles` mesaja bağlı (MCP
   * dışı) akışta da kullanılıyor; hepsini körlemesine temizlemek kullanıcının
   * sohbette gördüğü ilgisiz bir kartı kapatırdı.
   */
  const dismissActive = useCallback(() => {
    clearActiveCardRef.current?.();
    clearActiveCardRef.current = null;
    activeGateRef.current = null;
    setActiveGate(null);
  }, []);

  const poll = useCallback(async () => {
    if (!API) return;

    let pending: Record<string, any>;
    try {
      const res = await axios.get(`${API}/mcp-pending`);
      pending = res.data?.pending ?? {};
      failStreakRef.current = 0;
      alertedRef.current = false;
    } catch (err) {
      // Sessiz yutmak yok: yoklama artık koşulsuz koştuğu için kalıcı bir hata
      // (yanlış token → her saniye 401) hiçbir iz bırakmadan onay yolunu ölü
      // hale getiriyordu. Kullanıcı kartı beklerken köprü 180 sn sonra
      // reddediyor ve ekranda tek bir işaret bile olmuyordu.
      failStreakRef.current += 1;
      // Geliştirici sinyali HER hatada, kullanıcı sinyali eşikten sonra. Ayrım
      // kasıtlı: konsol ucuz ve teşhis için ilk hatadan itibaren gerekli, toast
      // ise her saniye tekrarlanırsa bilgi olmaktan çıkıp gürültü olur.
      console.warn(`[MCP] /mcp-pending erişilemiyor (${failStreakRef.current}. hata)`, err);
      if (failStreakRef.current >= POLL_FAILURE_ALERT_AFTER && !alertedRef.current) {
        alertedRef.current = true;  // her saniye tekrar basmasın
        showToast?.(cevir('mcp.pollUnreachable'), 'error');
      }
      return;
    }

    // Every entry goes to exactly one place, decided by its owner alone: a
    // chat (card slot, only while that chat is on screen) or the tray. That is
    // what keeps one gate from ever being drawn twice.
    const gatesByConv: Record<number, string[]> = {};
    const unknown: McpTrayGate[] = [];
    for (const [gateId, req] of Object.entries(pending)) {
      const owner = gateOwner(req);
      if (typeof owner === 'number') (gatesByConv[owner] ||= []).push(gateId);
      else if (owner === null) {
        const r = req as { tool?: string; params?: any; workspace_path?: string };
        unknown.push({ gateId, tool: String(r.tool ?? ''), params: r.params, workspacePath: r.workspace_path || '' });
      }
    }
    onOwnersChangeRef.current?.(gatesByConv);
    // Same ids, same entries: keep the old array so the tray does not
    // re-render on every poll.
    setUnknownGates(prev =>
      prev.length === unknown.length && prev.every((g, i) => g.gateId === unknown[i].gateId) ? prev : unknown);
    setSynced(true);

    // Açık bir kart varsa yenisini ALMA — kuyruk backend'de bekler.
    if (activeGateRef.current) {
      // İstisna: açık kartın gate'i backend'de artık yoksa (TTL süpürdü ya
      // da karar başka bir yoldan verildi) kart ZOMBİ demektir. Temizlenmezse
      // `activeGateRef` sonsuza dek dolu kalır ve BÜTÜN sonraki kartlar bloke
      // olurdu — kaldırdığımız kilidin daha kötüsü. The same goes for a card
      // whose chat is no longer on screen: it leaves the slot undecided and is
      // drawn again when its chat is opened, since it is still pending.
      const open = activeGateRef.current;
      if (open.gateId in pending && belongsOnScreen(open.conversationId, screenConvRef.current)) return;
      dismissActive();
    }

    for (const [gateId, req] of Object.entries(pending)) {
      const owner = gateOwner(req);
      if (!belongsOnScreen(owner, screenConvRef.current)) continue;
      const { tool, params, workspace_path: gateWorkspace } = req as {
        tool: string; params: any; workspace_path?: string;
      };

      // Kartı çizen tarafın gate kimliğini ve workspace'i okuyacağı TEK kayıt.
      // Eskiden kimlik `window.__mcpWriteGate` global'inde duruyordu ve iki
      // bekleyen yazma aynı slotu paylaşıyordu: "Yeni dosya" kartına basmak
      // ÖTEKİ isteğin gate'ini onaylıyordu (dış denetim: `approval-gate-
      // misbinding`, HIGH). Kimliği kartın kendi kaydında taşımak o sınıfı
      // bir örnek yamayarak değil kökten kapatıyor.
      const gate: McpActiveGate = { gateId, tool, workspacePath: gateWorkspace || '', conversationId: owner };
      activeGateRef.current = gate;
      setActiveGate(gate);

      // Eski üç kart PARAMETRE ŞEKLİNE bağlı: `params` bir nesne değilse
      // destructuring, `path` dize değilse `path.split` istisna atıyor. Ve
      // istisna gate KURULDUKTAN sonra düştüğü için `activeGateRef` dolu
      // kalıyor, yani yalnız o kart değil SONRAKİ BÜTÜN kartlar kayboluyordu
      // (denetim bulgusu, 31 Tem 2026). Şekli önceden ölçüp uymayanı genel
      // karta düşürmek, kartsız kalmaktan her durumda iyidir.
      const yuk = params && typeof params === 'object' ? params : {};
      const eskiKartCizilebilir =
        (tool === 'write_file' && typeof yuk.path === 'string') ||
        (tool === 'delete_file' && typeof yuk.path === 'string') ||
        (tool === 'bash' && typeof yuk.command === 'string');

      if (tool === 'write_file' && eskiKartCizilebilir) {
        const { path, content, original } = params;
        if (!original) {
          // Yeni dosya → FileCreationApproval
          const file: PendingFile = {
            name: path.split('/').pop() || path,
            code: content,
            suggestedPath: path,
            originalCode: '',
          };
          clearActiveCardRef.current = () => setPendingGenFiles(null);
          setPendingGenFiles({ files: [file], messageId: MCP_MSG_ID });
        } else {
          // Değişiklik → DiffViewer
          clearActiveCardRef.current = () => setPendingFix(null);
          setPendingFix({
            messageId: MCP_MSG_ID,
            applied: false,
            data: {
              original_code: original,
              fixed_code: content,
              explanation: cevir('mcp.updatingFile', { yol: path }),
              editor_hint: path,
            },
            gateId,
          });
        }
      } else if (tool === 'delete_file' && eskiKartCizilebilir) {
        clearActiveCardRef.current = () => setPendingDelete(null);
        setPendingDelete({ path: params.path, messageId: MCP_MSG_ID });
      } else if (tool === 'bash' && eskiKartCizilebilir) {
        clearActiveCardRef.current = () => setPendingCommand(null);
        setPendingCommand({ command: params.command, gateId, messageId: MCP_MSG_ID });
      } else {
        // Unity araçları ve tanınmayan her şey: TEK bir genel kart.
        //
        // ⚠️ Burası eskiden kartı hiç çizmiyor, gate'i bırakıp köprünün zaman
        // aşımına terk ediyordu. K1 kapısı kurulunca o dal ürünü kullanılamaz
        // hale getirdi: kütükteki 45 Unity aracının HİÇBİRİ yukarıdaki üç
        // eski adla (`write_file`/`delete_file`/`bash`) eşleşmiyor, yani adım
        // modunda her Unity yazması 180 sn bekleyip sessizce reddediliyordu ve
        // kullanıcıya hiç sorulmuyordu. Dış denetim bunu HIGH olarak buldu.
        //
        // Genel kart bilerek "tanınmayan"ı da kapsıyor: yeni bir araç
        // eklendiğinde kart kaybolmasın. Kartsız kalmak, kapının kullanıcıya
        // ulaşmadığı ve isteğin sessizce öldüğü hal demek.
        clearActiveCardRef.current = () => setPendingCommand(null);
        setPendingCommand({
          command: unityOzeti(tool, params),
          gateId,
          messageId: MCP_MSG_ID,
          kind: 'unity',
        });
      }
      break;  // aynı anda tek kart
    }
  }, [API, dismissActive, showToast, setPendingGenFiles, setPendingDelete, setPendingCommand, setPendingFix]);

  // Zamanlayicinin cagirdigi guncel `poll`. Bkz. asagidaki etkinin gerekcesi.
  const pollFnRef = useRef(poll);
  pollFnRef.current = poll;

  /**
   * Yoklamayı başlat/durdur. Koşulsuz açmanın maliyeti ÖLÇÜLDÜ (2026-07-29): rota
   * katmanında `/mcp-pending` çağrı başına **0,174 ms** (2000 çağrı / 0,347 sn,
   * TestClient üzerinden — yani gerçek TCP ve renderer maliyeti HARİÇ, bu bir
   * alt sınır). 1 sn aralıkta bu tek çekirdeğin ~%0,017'si. Eski koddaki
   * "idle'da CPU harcama" gerekçesi bu ölçümün karşısında duramıyor; karşılığı
   * ise kartın 8 sağlayıcıda hiç gelmemesiydi.
   *
   * SSE (kod yorumlarının iddia ettiği ama var olmayan çözüm) BİLEREK
   * seçilmedi: `EventSource` özel başlık gönderemiyor, dolayısıyla
   * `X-Session-Token` sorguya taşınırdı — sırrı URL'den başlığa taşıyan karar
   * (unity-mcp yerel auth) tam tersi yöndeydi. Geri almak için sebep yok.
   */
  useEffect(() => {
    if (!API || !enabled) {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
      return;
    }
    // İlk yoklama beklemeden: hook mount olmadan önce açılmış bir gate varsa
    // (köprü ürün penceresinden bağımsız çalışıyor) kart bir tam saniye geç
    // gelirdi. Açık kart kontrolü tekrar işlemeyi zaten engelliyor.
    // Zamanlayıcı `poll`un KİMLİĞİNE bağlı değil, bir ref üzerinden çağırıyor.
    // `poll`un bağımlılıkları çağıran taraftan geliyor (`showToast`,
    // `setPending*`) ve çoğu her render'da yeniden üretiliyor; bu etki `poll`a
    // bağlıyken her render aralığı yıkıp yeniden kuruyor ve ARADA fazladan bir
    // yoklama atıyordu. Ölçüldü 31 Ağu 2026: hook'a küçük bir durum eklemek
    // `/mcp-pending` çağrı sayısını 1'den 2'ye çıkardı.
    const tick = () => { void pollFnRef.current(); };
    tick();
    pollingRef.current = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
    };
  }, [API, enabled]);

  // Switching chats hands the slot to the new chat at once instead of on the
  // next tick. Skipped on mount: the effect above already polls then, and a
  // second mount poll is the doubling measured on 31 Aug 2026.
  const lastScreenRef = useRef(screenConvId);
  useEffect(() => {
    if (lastScreenRef.current === screenConvId) return;
    lastScreenRef.current = screenConvId;
    const open = activeGateRef.current;
    if (open && !belongsOnScreen(open.conversationId, screenConvId)) dismissActive();
    if (API && enabled) void pollFnRef.current();
  }, [API, enabled, screenConvId, dismissActive]);

  // Filtered at render time too: in the render where the screen changes, the
  // effect above has not run yet and the previous chat's card would flash in
  // the new one.
  const visibleGate = activeGate && belongsOnScreen(activeGate.conversationId, screenConvId)
    ? activeGate : null;

  /**
   * Karar verildikten (ya da kart kapatıldıktan) sonra çağrılır: kartı kaldırır
   * ve sıradaki isteğin gösterilmesine izin verir. Kararı GÖNDERMEZ — gönderme
   * kartın kendi handler'ında, çünkü her kartın toast metni farklı.
   */
  const resolveActiveGate = useCallback(() => { dismissActive(); }, [dismissActive]);

  /*
   * ⚠️ BURADA ESKİDEN DÖRT RESPONDER + `respond` VARDI, SİLİNDİLER (2026-07-29).
   *
   * `approveMCPFile`, `rejectMCPFile`, `approveMCPDelete`, `rejectMCPDelete` —
   * dördünün de ÜRÜNDE tek bir çağıranı yoktu (dış denetim
   * `test-only-routing-api-divergence`, depo çapında çağrı izlemesiyle
   * doğrulandı). Kartlar kararı `McpApprovalCards` içinden `postMcpDecision`
   * ile gönderiyor: farklı transport (fetch/axios), farklı sözleşme, farklı
   * kilit ömrü.
   *
   * Silinmelerinin sebebi düzen değil ÖLÇÜM: `approval-gate-feedback.test.ts`
   * bu ölü yolu MCP kart teslimi sanıp sınıyordu, dolayısıyla 230 testin hepsi
   * yeşilken gerçek kart yolundaki `stale-decision-latch` (kullanıcının gördüğü
   * ret yutuluyor, komut çalışıyor) hiç görünmüyordu. İki sözleşmeyi paralel
   * tutmak bu depodaki arızaların ortak biçimi; ikincisini silmek tek çözüm.
   */

  // `poll` dışarı da veriliyor: polling'in kendisi zamanlayıcıya bağlı, ama
  // "gate kimliği kartla birlikte taşınıyor mu" sorusu zamanlayıcıdan bağımsız
  // Urunde acik olan klasorun BACKEND'in gordugu hali. Normal yolda
  // `workspacePath`in aynisi; Docker modunda mount yolu. null = backend bu
  // klasoru adresleyemiyor, o zaman karsilastirilacak bir sey de yok.
  //
  // State, ref DEGIL. Once ref denendi (fazladan render dogurmasin diye) ve
  // TEST YAKALADI: kart geldiginde ref henuz doldurulmamis olabiliyor, yani
  // karsilastirma bir render boyunca eski degerle yapiliyordu. Fazladan render
  // sorunu asil yerinden cozuldu — yoklama etkisi artik `poll`un kimligine
  // bagli degil.
  //
  // Cevap HANGI YOL icin alindigini de tasiyor (`forPath`). Ayri bir "bitti"
  // bayragi degil, cunku bayrak effect icinde yaziliyor ve effect render'dan
  // SONRA kosuyor: acik klasor degistiginde bir render boyunca eski yolun
  // cevabi "bitmis" gorunurdu — kapatmaya calistigimiz pencerenin aynisi.
  // Sahiplik alani karsilastirmasi her render'da senkron dogru.
  const [mappedWorkspace, setMappedWorkspace] =
    useState<{ forPath: string; path: string | null } | null>(null);
  useEffect(() => {
    let iptal = false;
    if (!workspacePath) { setMappedWorkspace(null); return; }
    backendWorkspacePath(workspacePath).then(v => {
      if (!iptal) setMappedWorkspace({ forPath: workspacePath, path: v });
    });
    return () => { iptal = true; };
  }, [workspacePath]);

  // Ceviri henuz sonuclanmadi mi? Acik klasor YOKKEN "bekleniyor" degil:
  // orada cevrilecek bir sey de yok, karsilastirma kalici olarak bilinemez.
  const cevirmeBekliyor = !!workspacePath && mappedWorkspace?.forPath !== workspacePath;
  const backendFacingWorkspace = cevirmeBekliyor ? null : (mappedWorkspace?.path ?? null);

  // bir DOĞRULUK sorusu ve deterministik ölçülebilmeli.
  return {
    poll,
    activeGate: visibleGate,
    /** Requests with no known source chat, for the tray outside every chat. */
    unknownGates,
    /** At least one `/mcp-pending` answer has been applied. */
    synced,
    /** Gate'in workspace'i üründe açık olandan farklı mı (bilinmiyorsa false). */
    // Karsilastirma BACKEND ad alaninda yapilir. Gate'in tasidigi deger
    // backend'in kendi yolu (Docker'da `/workspace`); `workspacePath` ise
    // bilerek ana makinenin yolu. Ikisini ham karsilastirmak Docker modunda
    // HER onay kartinda kirmizi uyusmazlik bandi cikariyordu, yani gercek bir
    // capraz-proje istegi ayirt edilemez hale geliyordu (denetim, 31 Agu 2026).
    gateWorkspaceMismatch: workspaceMismatch(visibleGate?.workspacePath, backendFacingWorkspace),
    /**
     * Karsilastirma HENUZ YAPILAMIYOR — cevap yolda.
     *
     * Ayri bir sinyal olmasinin sebebi olculmus bir arizadir (dis denetim
     * bulgusu 4, 31 Agu 2026): yoklama ile ceviri iki ayri effect ve yoklama
     * once basliyor, yani BASKA bir projeye ait bir kart ceviri sonuclanmadan
     * cizilebiliyor. `workspaceMismatch` bilinmeyeni bilerek `false` sayiyor
     * (gerekcesi orada), ama banner o `false`i "dogrulandi, ayni proje" diye
     * ciziyordu: gri serit, "baska proje" yazisi yok, onay butonlari acik.
     * Kullanici tam o pencerede onaylayabiliyordu; kart saniyenin bir kismi
     * sonra kehribar rengine donuyordu. Kusur "bilinmeyen uyusmazlik degil"
     * degil, bilinmeyenin BILINEN ESLESME gibi sunulmasiydi.
     *
     * Yalniz gate bir workspace bildirdiyse anlamli: bildirmediyse cevirinin
     * sonucu ne olursa olsun karsilastirilacak bir sey yok, o bilinmezligi
     * banner zaten `mcp.sourceUnknown` ile soyluyor.
     */
    gateWorkspaceCheckPending: !!visibleGate?.workspacePath && cevirmeBekliyor,
    openWorkspacePath: workspacePath,
    resolveActiveGate,
  };
};
