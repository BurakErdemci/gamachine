/**
 * McpApprovalCards — unityMCP köprüsünden gelen onay kartlarının TEK çizim yeri.
 *
 * Neden ayrı bileşen (2026-07-29 denetimi, `approval-card-hidden-by-view-state`):
 * kartlar ChatPanel'in içinde çiziliyordu, ChatPanel ise yalnız bir workspace
 * AÇIKKEN mount ediliyor (`home.tsx`, `if (!fs.workspacePath) return
 * <WorkspaceScreen/>`). Köprü ürünün görünüm durumundan bağımsız çalıştığı için
 * workspace seçilmemişken gelen istek state'e giriyor, ekrana hiç çıkmıyor ve
 * 180 sn sonra reddediliyordu. Kartları kendi bileşenine almak, onları hem
 * sohbetin içinde hem de workspace ekranının üstünde çizilebilir kılıyor —
 * markup'ı kopyalamadan.
 *
 * ⚠️ `pendingFix` (var olan dosyayı değiştirme) burada KENDİ DiffViewer'ını
 * kuruyor; ChatPanel'deki mesaja bağlı DiffViewer'ın kopyası DEĞİL. İki akış
 * gerçekten farklı: burada karar köprüye POST'lanır, orada dosya IPC ile
 * yazılır. 2026-07-29'da bunlar "aynı eleman" diye tek yere toplanmıştı; o
 * birleştirme kopyala-yapıştır sapmasını engellemek içindi ve akışların
 * ayrılmasıyla gereksizleşti.
 */
import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, FolderOpen, Loader2 } from 'lucide-react';
import { DiffViewer } from './DiffViewer';
import { FileCreationApproval, PendingFile } from './FileCreationApproval';
import { FileDeleteApproval } from './FileDeleteApproval';
import { CommandApproval } from './CommandApproval';
import { RiskReasonLine } from './RiskReasonLine';
import { GateFailure, postMcpDecision, decisionToast } from '../../hooks/home/gateResponse';
import { MCP_MSG_ID, McpActiveGate } from '../../hooks/home/useMCPApproval';
import { useLang, type TKey } from '../../lib/i18n';

/**
 * Bir karar denemesinin sonucu — ÜÇ durum, iki değil.
 *
 * Neden ayrı tip (dış denetim `stale-decision-latch`, 2026-07-29): eskiden
 * `decide` bastırılmış çağrıya `null` dönüyordu ve `null` bu depodaki gate
 * sözleşmesinde "TESLİM EDİLDİ" demek (bkz gateResponse.ts). Yani gönderilmemiş
 * bir karar, gönderilmiş gibi raporlanıyordu. Ölçülen dizi: onay POST'u
 * uçuştayken İptal'e basmak → ret bastırılıyor → ekrana "Komut iptal edildi"
 * yazılıyor → onay iniyor ve KOMUT ÇALIŞIYOR. Kullanıcının gördüğü son karar
 * ret, gerçekleşen ise onay.
 *
 * `sent: false` bir hata değil, "bu çağrıda hiçbir şey gönderilmedi" bilgisidir;
 * çağrı yerleri buna göre farklı davranıyor (kapatma yolunda sessiz, kullanıcının
 * bastığı butonda sesli).
 */
type DecisionResult =
  | { sent: true; failure: GateFailure | null }
  | { sent: false; reason: 'in-flight' | 'already-decided' };

/**
 * Bastırılan karar için kullanıcıya ne denir.
 *
 * İkisi de `warning`: kullanıcı bir buton bastı ve o basış bir şey YAPMADI —
 * bunu `info` diye göstermek, düzelttiğimiz sessiz yalanın kibar hâli olurdu.
 * İki durum ayrı cümle çünkü kullanıcının yapabileceği şey farklı: uçuştaki
 * karar birazdan sonuçlanır ve toast'ı gelir; sonuçlanmış karar geri alınamaz.
 */
// Tablo modul duzeyinde sabit oldugu icin ANAHTAR saklaniyor, metin degil:
// `t()` ancak render aninda cagrilabilir.
const SUPPRESSED_MESSAGE_KEYS: Record<'in-flight' | 'already-decided', TKey> = {
  'in-flight': 'mcp.suppressedInFlight',
  'already-decided': 'mcp.suppressedDecided',
};

interface McpApprovalCardsProps {
  /** Ekranda karar bekleyen istek. `null` ise hiçbir şey çizilmez. */
  gate: McpActiveGate | null;
  /** Gate'in workspace'i üründe açık olandan farklı mı (bilinmiyorsa false). */
  workspaceMismatch: boolean;
  /**
   * Karşılaştırma HENÜZ yapılamıyor (açık klasörün backend'deki karşılığı
   * yolda). Opsiyonel: verilmezse eski davranış, yani "karşılaştırma yapıldı".
   * `workspaceMismatch` ile aynı anda doğru olamaz — bilinmeyen bir uyuşmazlık
   * iddia edilmiyor, yalnız eşleşme İDDİA EDİLMİYOR.
   */
  workspaceCheckPending?: boolean;
  /** Üründe açık olan workspace; banner'da "açık olan" satırı için. */
  openWorkspacePath: string | null;
  /** Karar verildi/kart kapandı → sıradaki isteğin gösterilmesine izin ver. */
  onResolved: () => void;

  apiBase: string;
  sessionToken: string;
  showToast: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
  refreshFileTree: () => void;

  pendingGenFiles: { files: PendingFile[]; messageId: number } | null;
  setPendingGenFiles: (val: any) => void;
  pendingDelete: { path: string; messageId: number } | null;
  setPendingDelete: (val: any) => void;
  pendingCommand: { command: string; gateId: string; messageId: number; kind?: 'shell' | 'unity' | 'mail' } | null;
  setPendingCommand: (val: any) => void;
  pendingFix: { data: any; messageId?: number; applied?: boolean; gateId?: string } | null;
  setPendingFix: (val: any) => void;

  /** Editör bağlamı. Workspace ekranının üstünde çizilirken yoktur. */
  setDiffFile?: (val: any) => void;
  onOpenFile?: (path: string) => void;
  /** Onay sonrası editör tamponunu tazelemek için; editör yoksa verilmez. */
  setCode?: (code: string) => void;
  /** A paired phone can decide these cards too ("Or approve on your phone"). */
  phonePaired?: boolean;
}

/**
 * İsteğin hangi projeden geldiğini yazan şerit.
 *
 * Kullanıcı kararı (2026-07-29): eşleşmeyen istek GİZLENMEZ, çünkü unityMCP'yi
 * doğrudan başka bir istemciye bağlamış kullanıcıyı 180 sn'lik sessiz bir redde
 * kilitlerdi. Bunun bedeli açıkça kabul edildi: koruma bu şeridin OKUNMASINA
 * bağlı, yani refleks onayda hiçbir şey engellemiyor.
 *
 * Şerit eşleşme durumunda da çiziliyor. Yalnız uyuşmazlıkta göstermek, kartın
 * "hangi projeye ait" bilgisini normal akışta hiç öğretmezdi; kullanıcı şeridi
 * ilk kez tam da en riskli anda görürdü ve neye baktığını bilmezdi.
 */
const WorkspaceBanner: React.FC<{
  gate: McpActiveGate;
  mismatch: boolean;
  checking: boolean;
  openWorkspacePath: string | null;
}> = ({ gate, mismatch, checking, openWorkspacePath }) => {
  const { t } = useLang();
  // Gate workspace'i boşsa iddia edilecek bir şey yok — bilinmiyor de.
  const bilinmiyor = !gate.workspacePath;
  // ÜÇ durum, iki değil. Üçüncüsü (`checking`) bir denetim bulgusundan doğdu
  // (31 Ağu 2026): karşılaştırma yapılamadığı sürece şerit gri çiziliyordu ve
  // gri, kullanıcı için "baktım, aynı proje" demek — yani kanıtlanmamış bir
  // eşleşme kanıtlanmış gibi sunuluyordu. Kart yine de çiziliyor ve butonlar
  // AÇIK kalıyor: kartı beklemek ya da butonları kilitlemek, cevap hiç
  // gelmediğinde isteği 180 sn'lik sessiz redde kilitlerdi — bu bileşenin
  // baştan beri reddettiği hâl (bkz. yukarıdaki kullanıcı kararı).
  // Drawn on tokens as a strip on top of the card; the state is an attribute, so the styling
  // (and any test) reads `data-source` rather than a colour class.
  const durum = mismatch ? 'mismatch' : checking ? 'checking' : 'match';
  return (
    <div className="approval-src" data-source={durum}>
      {mismatch ? <AlertTriangle size={14} className="approval-src-ic" aria-hidden="true" />
                : checking ? <Loader2 size={14} className="approval-src-ic animate-spin" aria-hidden="true" />
                : <FolderOpen size={14} className="approval-src-ic" aria-hidden="true" />}
      <div className="approval-src-text">
        {mismatch && <div className="approval-src-k">{t('mcp.otherProject')}</div>}
        {!mismatch && checking && <div className="approval-src-k">{t('mcp.workspaceChecking')}</div>}
        <div className="approval-src-path">
          {bilinmiyor ? t('mcp.sourceUnknown') : gate.workspacePath}
        </div>
        {!mismatch && checking && (
          <div className="approval-src-hint">{t('mcp.workspaceCheckingHint')}</div>
        )}
        {mismatch && openWorkspacePath && (
          <div className="approval-src-hint">{t('mcp.openWorkspace')} {openWorkspacePath}</div>
        )}
      </div>
    </div>
  );
};

export const McpApprovalCards: React.FC<McpApprovalCardsProps> = ({
  gate,
  workspaceMismatch,
  workspaceCheckPending = false,
  openWorkspacePath,
  onResolved,
  apiBase,
  sessionToken,
  showToast,
  refreshFileTree,
  pendingGenFiles,
  setPendingGenFiles,
  pendingDelete,
  setPendingDelete,
  pendingCommand,
  setPendingCommand,
  pendingFix,
  setPendingFix,
  setDiffFile,
  onOpenFile,
  setCode,
  phonePaired,
}) => {
  const { t } = useLang();
  /**
   * Bu SUNUM için karar sonuçlandı mı (gate id ile).
   *
   * Gerekçesi iki yönlü. (1) Kart kapatma yolları (`onDone`, `onSkipOne`) artık
   * karar gönderiyor — eskiden yalnız state'i temizliyorlardı ve köprü 180 sn
   * bekleyip reddediyordu; kullanıcı ise reddettiğini SANIYORDU (dış denetim:
   * `approval-state-cleared-without-decision`). (2) Ama `onDone` onaydan SONRA
   * da tetikleniyor; işaret olmasa onaylanmış bir gate'e ikinci kez "reddet"
   * gider ve kullanıcı sebepsiz bir "gate bulunamadı" uyarısı görürdü.
   *
   * ⚠️ Bayrak POST SONUÇLANDIKTAN sonra konuyor, öncesinde değil. Önce konması
   * `stale-decision-latch` bulgusunun yarısıydı: uçuştaki onayın süresi boyunca
   * bayrak "karar verildi" diyordu ama ortada henüz bir karar yoktu, ve o
   * penceredeki her buton basışı sessizce yutuluyordu. Uçuş penceresi ayrı bir
   * kilitle (`inFlightRef`) kapatılıyor — iki farklı durum, iki farklı işaret.
   */
  const decidedRef = useRef<string | null>(null);
  /** POST'u şu an uçuşta olan gate. Senkron yazılır: iki tıklama arasında await yok. */
  const inFlightRef = useRef<string | null>(null);
  /** Uçuş sırasında kartı kilitlemek için — ref değil state, çünkü çizimi etkiliyor. */
  const [busy, setBusy] = useState(false);

  /**
   * Kart ekrandan kalkınca bayrak düşer.
   *
   * Neden gerekli (`stale-decision-latch`'in ikinci yarısı): bileşen ChatPanel
   * içinde MOUNT KALIYOR, yalnız `gate` null oluyor. Bayrak gate id'sine
   * ömür boyu bağlı kalsaydı, teslimatı başarısız olmuş bir istek backend'de
   * hâlâ bekliyorken yeniden sunulduğunda ikinci karar da bastırılırdı — yani
   * kullanıcı o isteğe BİR DAHA karar veremezdi.
   *
   * Yeniden sunum fail-open değil: backend karar alınca kaydı `_mcp_pending`'den
   * DÜŞÜRÜYOR, dolayısıyla orada yeniden görünen bir gate'in kaydedilmiş kararı
   * yok demektir. Kararın gerçekten geç kaldığı durumda ikinci POST
   * `gate_not_found` alır ve kullanıcı bunu uyarı olarak görür.
   */
  useEffect(() => {
    if (!gate) decidedRef.current = null;
  }, [gate]);

  if (!gate) return null;

  const gateId = gate.gateId;

  /** Kararı gönderir; uçuşta ya da sonuçlanmış bir karar varsa GÖNDERMEZ ve bunu söyler. */
  const decide = async (approved: boolean): Promise<DecisionResult> => {
    if (inFlightRef.current === gateId) return { sent: false, reason: 'in-flight' };
    if (decidedRef.current === gateId) return { sent: false, reason: 'already-decided' };
    inFlightRef.current = gateId;
    setBusy(true);
    try {
      const failure = await postMcpDecision(apiBase, gateId, approved, sessionToken);
      decidedRef.current = gateId;
      return { sent: true, failure };
    } finally {
      inFlightRef.current = null;
      setBusy(false);
    }
  };

  /**
   * Kararın sonucunu kullanıcıya bildirir.
   *
   * Bastırılan çağrı ile teslim edilen çağrı AYNI cümleyi göremez — bulgunun
   * tam olarak ölçtüğü şey buydu. `decisionToast` yalnız gerçekten gönderilmiş
   * bir kararın teslimatını yorumlar; gönderilmemişi yorumlamaya çalışmak, ona
   * olmayan bir anlam yüklemek olurdu.
   */
  // `=== false`, `!result.sent` DEĞİL: bu depoda `strict: false` (tsconfig:11)
  // ve truthiness daraltması ayrık birleşimi ayırmıyor; literal karşılaştırma
  // ayırıyor. Aynı sebep aşağıdaki iki çağrı yerinde de geçerli.
  const reportDecision = (result: DecisionResult, deliveredMessage: string) => {
    if (result.sent === false) {
      showToast(t(SUPPRESSED_MESSAGE_KEYS[result.reason]), 'warning');
      return;
    }
    const bildirim = decisionToast(result.failure, deliveredMessage);
    showToast(bildirim.message, bildirim.type);
  };

  /**
   * Kart kapatıldı → karar verilmemişse köprüyü bekletmeden reddet.
   *
   * Koşul BURADA YOK, `decide` içinde: iki yerde aynı kontrolü yapmak
   * savunma değil fazlalıktı. Mutasyonla ölçüldü (2026-07-29): kontrollerden
   * biri kaldırıldığında öteki testi yeşil tutuyordu, yani ikisi de tek tek
   * "gereksiz" görünüyordu ve hangisinin gerçek koruma olduğu ölçülemiyordu.
   * Tek kontrol, tek mutasyon, net cevap.
   *
   * Bastırılma burada BEKLENEN durum (kararlar bittikten sonra "Kapat"a
   * basılıyor), o yüzden sessiz: kullanıcı bir KARAR vermedi, kartı topladı.
   *
   * ⚠️ Bu sessizlik YALNIZ bu anlama uygulanır. Kullanıcının görünür "vazgeç"
   * kararı için `rejectByUser` var — ikisini tek fonksiyona toplamak, iki-varyant
   * turunda ölçülen sessiz arızanın ta kendisiydi (2026-07-29): "İptal"e basan
   * kullanıcının reddi, onay uçuştayken hiçbir iz bırakmadan yutuluyordu.
   */
  const closeWithoutDecision = async () => {
    const result = await decide(false);   // zaten karar verildiyse no-op
    if (result.sent && result.failure) showToast(result.failure.message, result.failure.type);
    onResolved();
  };

  /**
   * Kullanıcının GÖRÜNÜR reddi (İptal / Vazgeç / Reddet / Atla).
   *
   * Teslim edilen ret sessizdir — kart kaybolur, söylenecek bir şey yok. Ama
   * GÖNDERİLEMEYEN ret asla sessiz kalamaz: kullanıcı reddettiğini sanır, işlem
   * olur. Dört kart yolunun dördü de buradan geçiyor; ayrı ayrı yazıldığında
   * biri unutuluyordu (ölçüldü: `onSkipOne` ve oluşturma kartının "İptal"i).
   */
  const rejectByUser = async () => {
    const result = await decide(false);
    if (result.sent === false) showToast(t(SUPPRESSED_MESSAGE_KEYS[result.reason]), 'warning');
    else if (result.failure) showToast(result.failure.message, result.failure.type);
    onResolved();
  };

  /**
   * Karar uçuştayken kartı kilitler.
   *
   * `fieldset[disabled]` seçilmesinin sebebi: içindeki BÜTÜN butonları
   * tarayıcının kendi kurallarıyla devre dışı bırakıyor (klavye dahil), yani
   * dört kart bileşenine ayrı ayrı `disabled` prop'u eklemeye ve o dördünün
   * birbiriyle uyuşmasını ummaya gerek kalmıyor — bu depodaki arızaların ortak
   * biçimi tam olarak "uyuşması gereken iki yer uyuşmuyor".
   *
   * ⚠️ Bu görsel kilit GARANTİ DEĞİL, ikinci savunma hattı: asıl güvence
   * `decide` içindeki mantıksal kilit. Kilidin kendisi tek başına ölçülemez
   * (jsdom `fieldset` devralmasını tam uygulamıyor), o yüzden testler mantık
   * katmanını ölçüyor.
   */
  const lock = (node: React.ReactNode) => (
    <fieldset disabled={busy} aria-busy={busy} className="contents">{node}</fieldset>
  );

  // The source strip sits on top of every card. The risk line goes INTO the card as its
  // `.approval-why` (mockup); the diff viewer has no such slot yet, so for it alone the line
  // rides under the strip. Either way it renders nothing for cards that auto/step raised.
  const banner = (withRisk: boolean) => (
    <>
      <WorkspaceBanner
        gate={gate}
        mismatch={workspaceMismatch}
        checking={workspaceCheckPending && !workspaceMismatch}
        openWorkspacePath={openWorkspacePath}
      />
      {withRisk && <RiskReasonLine reason={gate.riskReason} detail={gate.riskDetail} className="approval-src-risk" />}
    </>
  );
  const risk = { riskReason: gate.riskReason, riskDetail: gate.riskDetail, phonePaired };

  return (
    <>
      {pendingGenFiles?.messageId === MCP_MSG_ID && (
        <div className="approval-slot">
          {banner(false)}
          {lock(
          <FileCreationApproval
            files={pendingGenFiles.files}
            {...risk}
            autoAccept={false}
            setDiffFile={setDiffFile ?? (() => {})}
            onOpenFile={onOpenFile ?? (() => {})}
            // `gateId ? … : null` üçlemesi kaldırıldı: `null` bu sözleşmede
            // "teslim edildi" demek, yani gate yokken hiçbir istek gitmeden
            // başarı iddia ediliyordu. Kararı artık postMcpDecision veriyor.
            onAcceptOne={async (file) => {
              const result = await decide(true);
              refreshFileTree();
              // "oluşturuldu" DEĞİL "yazılıyor": teslimat yalnız backend'in
              // onayı kaydettiğini kanıtlıyor. Dosyayı MCP köprüsü bundan sonra
              // `file_tools.write_file` içinde yazıyor ve orada düşebiliyor
              // (ölçüldü 2026-07-29: hedefin üst dizini normal bir dosyaysa
              // `os.makedirs` → FileExistsError, dosya diske hiç yazılmıyor —
              // ekranda ise "✅ oluşturuldu" duruyordu).
              reportDecision(result, t('mcp.sentWriteFile', { ad: file.name }));
              // ⚠️ BİLİNEN BOŞLUK: dönen `true` FileCreationApproval'ın kartında
              // dosyayı "oluşturuldu" listesine taşıyor. O sözleşme (bkz.
              // FileCreationApproval.tsx:34) yerel IPC yolunda GERÇEKTEN
              // doğrulanmış başarı demek; burada yalnız teslimat demek. Üçüncü
              // bir durum ("teslim edildi, sonuç bilinmiyor") olmadan
              // düzeltilemez ve `false` dönmek daha kötü olurdu: hiç olmamış
              // bir başarısızlığı iddia ederdi.
              // Bastırılan çağrı da `false` döner: hiçbir şey gönderilmediği
              // için dosyayı "oluşturuldu" listesine taşımak yalan olurdu.
              return result.sent && !result.failure;
            }}
            // Atlamak da bir KARAR: eskiden hiçbir şey göndermiyordu ve istek
            // köprüde 180 sn asılı kalıyordu. Bastırılırsa da sessiz kalmaz.
            onSkipOne={() => { void rejectByUser(); }}
            onAcceptAll={async () => {
              const result = await decide(true);
              refreshFileTree();
              // Kardeş satır: yukarıdaki tek-dosya yoluyla AYNI sınıf, aynı dil.
              reportDecision(result, t('mcp.sentWriteFiles'));
              return result.sent && !result.failure;
            }}
            // "Kapat" (kararlar bitti) ile "İptal" (kullanıcının kararı) AYRI:
            // ikisi tek prop'tayken İptal'in reddi sessizce yutuluyordu.
            onDone={() => { setPendingGenFiles(null); void closeWithoutDecision(); }}
            onCancel={() => { setPendingGenFiles(null); void rejectByUser(); }}
          />)}
        </div>
      )}

      {pendingDelete?.messageId === MCP_MSG_ID && (
        <div className="approval-slot">
          {banner(false)}
          {lock(
          <FileDeleteApproval
            path={pendingDelete.path}
            {...risk}
            onConfirm={async () => {
              const result = await decide(true);
              setPendingDelete(null);
              onResolved();
              // MCP server approval'ı polling ile ~500ms gecikmeli görür → dosyayı siler.
              // Refresh'i geciktirmezsek dosya henüz silinmemiş olur.
              setTimeout(() => refreshFileTree(), 900);
              // "silindi" DEĞİL "siliniyor": onayın iletilmesi silmenin
              // yapıldığını değil, köprünün onu ~500ms sonra DENEYECEĞİNİ
              // gösteriyor (üstteki refreshFileTree gecikmesi tam bu yüzden var).
              reportDecision(result, t('mcp.sentDelete'));
            }}
            onCancel={() => { setPendingDelete(null); void rejectByUser(); }}
          />)}
        </div>
      )}

      {pendingCommand?.messageId === MCP_MSG_ID && (
        <div className="approval-slot">
          {banner(false)}
          {lock(
          <CommandApproval
            command={pendingCommand.command}
            kind={pendingCommand.kind}
            {...risk}
            onConfirm={async () => {
              const result = await decide(true);
              setPendingCommand(null);
              onResolved();
              // "çalışıyor" iddiası onay iletilse BİLE erken: komutu köprü
              // bundan sonra başlatıyor ve başlatma da düşebilir.
              reportDecision(result, t(pendingCommand.kind === 'mail' ? 'mcp.sentMail' : 'mcp.sentCommand'));
            }}
            onCancel={async () => {
              const result = await decide(false);
              setPendingCommand(null);
              onResolved();
              // RET tarafı ASİMETRİK ve bilerek "oldu" diyor: köprü kararı
              // fail-closed okuyor, yani iletilmiş bir ret komutun
              // ÇALIŞMAYACAĞINI garanti ediyor — onaydan farklı olarak burada
              // teslimat sonucun kendisi. (Onay tarafı bunu söyleyemez: orada
              // teslimattan sonra hâlâ yapılacak bir iş kalıyor.)
              //
              // ⚠️ Bu asimetri YALNIZ ret GERÇEKTEN GÖNDERİLDİYSE geçerli.
              // `stale-decision-latch` tam olarak burada üredi: onay uçuştayken
              // basılan İptal bastırılıyor, `reportDecision` olmadan ekrana
              // "Komut iptal edildi" yazılıyor ve komut çalışıyordu — yani
              // fail-closed garantisi hiç var olmayan bir POST'a dayandırılıyordu.
              reportDecision(result, t('mcp.commandCancelled'));
            }}
          />)}
        </div>
      )}

      {pendingFix?.messageId === MCP_MSG_ID && (
        <div className="approval-slot">
          {banner(true)}
          {lock(
          <DiffViewer
            diffData={pendingFix.data}
            filename={pendingFix.data?.editor_hint?.split('/').pop()}
            applied={pendingFix.applied}
            onAccept={async (fixedCode) => {
              const result = await decide(true);
              // Dosyayı MCP köprüsü yazıyor; burada yalnız editör tamponu
              // tazeleniyor ve yalnız karar İLETİLDİYSE — iletilmediyse diskte
              // eski içerik kalacak, editörde yenisini göstermek yalan olurdu.
              // Bastırılan çağrıda da tazelenmiyor: gönderilmemiş bir onayın
              // sonucunu editörde göstermek aynı yalanın başka biçimi.
              if (result.sent && !result.failure) setCode?.(fixedCode);
              setPendingFix(null);
              onResolved();
              refreshFileTree();
              // "Onaylandı" değil "gönderildi": dosyayı MCP köprüsü
              // BUNDAN SONRA yazıyor ve orada düşebiliyor.
              reportDecision(result, t('mcp.sentDiff'));
            }}
            onReject={() => { setPendingFix(null); void rejectByUser(); }}
          />)}
        </div>
      )}
    </>
  );
};
