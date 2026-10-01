import { useLang } from '../../lib/i18n';
import { CheckCircle2, FileCode, SkipForward, XCircle, Eye } from 'lucide-react';
import { ApprovalCard, CheckIcon } from './ApprovalCard';
import { RiskReasonLine } from './RiskReasonLine';
import { useState, useEffect } from 'react';

export interface PendingFile {
  name: string;
  code: string;
  suggestedPath: string;
  originalCode?: string;
}

interface FileCreationApprovalProps {
  files: PendingFile[];
  /**
   * `true` = dosya GERÇEKTEN yazıldı/onay iletildi. Yalnız `true` dönerse kart
   * dosyayı "oluşturuldu" sayar.
   *
   * Neden `Promise<void>` değil (ölçüldü 2026-07-29): tek sinyal istisnaydı ve
   * asıl yazma yolu HİÇ throw etmiyor — `ipc.invoke('write-file')`
   * `{success:false, error}` dönüyor (main/background.ts:397-418). Yani
   * `.catch()` hiç ateşlenmiyordu ve reddedilen dosya "İşlem Tamamlandı"
   * kartında oluşturulmuş gibi listeleniyordu.
   */
  onAcceptOne: (file: PendingFile) => Promise<boolean>;
  onSkipOne: (file: PendingFile) => void;
  /** `true` = dosyaların HEPSİ yazıldı. Kısmi başarı `false`'tur; hangi dosyanın
   *  neden yazılamadığını çağıran kendi bildirir (dosya başına toast). */
  onAcceptAll: (files: PendingFile[]) => Promise<boolean>;
  /** Kararlar bittikten SONRA kartı kapatır ("Kapat"). Bir karar DEĞİLDİR. */
  onDone: () => void;
  /**
   * Kullanıcı karar vermeden kartı kapattı ("İptal") — bu bir KARARDIR.
   *
   * Neden `onDone`'dan ayrı (ölçüldü 2026-07-29, iki-varyant turu): tek prop iki
   * zıt anlamı taşıyordu. Çağıran taraf `onDone`'u "akış kendiliğinden kapandı"
   * diye okuyup bastırılan kararı SESSİZ geçiyordu; aynı sessizlik "İptal"e
   * basan kullanıcıya da uygulanınca, onay uçuştayken basılan İptal hiçbir iz
   * bırakmadan yutuluyordu — `stale-decision-latch` sınıfının sessiz biçimi.
   *
   * Opsiyonel ve varsayılanı `onDone`: mevcut çağıranlar (ChatPanel'in sohbet
   * akışı) davranışlarını aynen koruyor, ayrımı yalnız ihtiyacı olan taraf yapar.
   */
  onCancel?: () => void;
  onOpenFile?: (path: string) => void;
  autoAccept?: boolean;
  setDiffFile: (file: PendingFile | null) => void;
  /** Why balanced mode stopped here; absent for auto/step cards. */
  riskReason?: string;
  riskDetail?: string;
  /** A paired phone can decide this card too. */
  phonePaired?: boolean;
}

export const FileCreationApproval = ({
  files,
  onAcceptOne,
  onSkipOne,
  onAcceptAll,
  onDone,
  onCancel,
  onOpenFile,
  autoAccept,
  setDiffFile,
  riskReason,
  riskDetail,
  phonePaired,
}: FileCreationApprovalProps) => {
  const { t } = useLang();
  const [currentIdx, setCurrentIdx] = useState(0);
  const [done, setDone] = useState<Set<number>>(new Set());
  const [skipped, setSkipped] = useState<Set<number>>(new Set());
  // Yazılamayanlar ayrı tutulur: `done`'a katmak yalanın ta kendisiydi,
  // `skipped`'a katmak da yanlış olurdu (kullanıcı atlamadı, sistem reddetti).
  const [failed, setFailed] = useState<Set<number>>(new Set());
  // "Tümünü Onayla" başarısızlığı ayrı bir bayrak: toplu yolda hangi dosyanın
  // yazıldığı bilinmiyor, o yüzden dosya bazında işaretleme YAPILMIYOR.
  const [bulkFailed, setBulkFailed] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [allDone, setAllDone] = useState(false);

  const remaining = files.length - done.size - skipped.size - failed.size;

  // Dosya değiştiğinde ana editörü güncelle
  useEffect(() => {
    if (!allDone && files[currentIdx]) {
      setDiffFile(files[currentIdx]);
    }
    return () => setDiffFile(null);
  }, [currentIdx, allDone, files]);

  useEffect(() => {
    if (autoAccept && !allDone && !processing && files.length > 0) {
      handleAcceptAll();
    }
  }, [autoAccept]);

  /**
   * Bir dosya çözüldükten (yazıldı / atlandı / başarısız) sonra sıradakine geç,
   * hepsi çözüldüyse özet kartına düş. `done`/`skipped`/`failed` bu noktada hâlâ
   * bayat (setState henüz uygulanmadı), o yüzden çözülen dosya `+1` ile sayılır —
   * mevcut davranış, korundu.
   */
  const advance = (idx: number) => {
    const next = files.findIndex(
      (_, i) => !done.has(i) && !skipped.has(i) && !failed.has(i) && i !== idx,
    );
    if (next !== -1) setCurrentIdx(next);
    else if (done.size + skipped.size + failed.size + 1 === files.length) {
      setAllDone(true);
      setDiffFile(null);
    }
  };

  const handleAccept = async (idx: number) => {
    const file = files[idx];
    if (!file || processing || done.has(idx)) return;
    setProcessing(true);
    // Eskiden burada `.catch()` + KOŞULSUZ `setDone` vardı. Yazma yolu hiç throw
    // etmediği için `.catch` ölü koddu ve reddedilen dosya "oluşturuldu"
    // listesine giriyordu. `.catch` yine duruyor (beklenmedik istisna hâlâ
    // olabilir) ama artık bir DEĞERE — `false`'a — çevriliyor.
    const ok = await onAcceptOne(file).catch(err => { console.error(err); return false; });
    setProcessing(false);
    if (ok) setDone(prev => new Set([...prev, idx]));
    else setFailed(prev => new Set([...prev, idx]));
    advance(idx);
  };

  const handleSkip = (idx: number) => {
    const file = files[idx];
    if (!file || done.has(idx)) return;
    onSkipOne(file);
    setSkipped(prev => new Set([...prev, idx]));
    advance(idx);
  };

  const handleAcceptAll = async () => {
    if (processing) return;
    setProcessing(true);
    const ok = await onAcceptAll(files).catch(err => { console.error(err); return false; });
    setProcessing(false);
    // Kısmi başarı `false` sayılıyor. Ama başarısızlıkta dosyalar TEK TEK
    // "yazılamadı" diye işaretlenmiyor: bu bileşen hangisinin yazıldığını
    // bilmiyor ve yazılmış bir dosyaya "yazılamadı" demek, kapattığımız yalanın
    // ayna görüntüsü olurdu. Bilinen tek şey "hepsi başarılı değil" — kart da
    // yalnız onu söyler.
    if (ok) setDone(new Set(files.map((_, i) => i)));
    else setBulkFailed(true);
    setAllDone(true);
    setDiffFile(null);
  };

  if (allDone) {
    const createdFiles = files.filter((_, i) => done.has(i));
    const failedFiles = files.filter((_, i) => failed.has(i));
    // Tek bir başarısızlık bile "İşlem Tamamlandı" başlığını hak etmiyor:
    // kullanıcının o başlıktan çıkardığı sonuç "dosyalar diskte" oluyor.
    const hasFailure = bulkFailed || failedFiles.length > 0;
    // The decided record (mockup: a compact log line with the stamp). Only a fully written
    // set earns the "approved / done" stamp; any failure is a plain record without one.
    return (
      <ApprovalCard
        kind="create"
        testId="create-approval"
        state={hasFailure ? 'failed' : 'approved'}
        title={hasFailure ? t('approval.failed') : t('approval.done')}
        name={files.length === 1 ? t('card.nameCreateOne', { ad: files[0].name }) : t('card.nameCreate', { sayi: files.length })}
        body={(
          <ul className="approval-files">
            {createdFiles.map((file) => (
              <li key={file.suggestedPath} data-file-state="written">
                <FileCode size={14} aria-hidden="true" />
                <span>{file.name}</span>
              </li>
            ))}
            {failedFiles.map((file) => (
              <li key={`failed-${file.suggestedPath}`} data-file-state="failed">
                <XCircle size={14} aria-hidden="true" />
                <span>{file.name}</span>
              </li>
            ))}
            {bulkFailed && (
              <li data-file-state="unknown"><span>{t('approval.verifyTree')}</span></li>
            )}
          </ul>
        )}
        dismiss={<button type="button" onClick={onDone} className="btn btn-ghost">{t('approval.close')}</button>}
      />
    );
  }

  return (
    <ApprovalCard
      kind="create"
      testId="create-approval"
      who={t('approval.title')}
      name={files.length === 1 ? t('card.nameCreateOne', { ad: files[0].name }) : t('card.nameCreate', { sayi: files.length })}
      sentence={<span className="num">{remaining} {t('approval.pending')}</span>}
      why={riskReason ? <RiskReasonLine reason={riskReason} detail={riskDetail} /> : undefined}
      phoneHint={phonePaired}
      body={(
        <ul className="approval-files custom-scrollbar">
          {files.map((file, i) => {
            const isDone = done.has(i);
            const isSkipped = skipped.has(i);
            const isFailed = failed.has(i);
            const isActive = i === currentIdx;
            // Yazılamayan dosya "bekliyor" sayılmaz: sayaçla (remaining) ve
            // ilerleme mantığıyla (advance) aynı tanımı kullanmazsa liste
            // "0 bekliyor" derken hâlâ Uygula butonu gösterirdi.
            const isPending = !isDone && !isSkipped && !isFailed;
            const fileState = isDone ? 'written' : isFailed ? 'failed' : isSkipped ? 'skipped' : 'pending';

            return (
              <li
                key={i}
                data-file-state={fileState}
                data-active={isActive || undefined}
                onClick={() => isPending && setCurrentIdx(i)}
                className="approval-file"
              >
                {isDone ? <CheckCircle2 size={14} aria-hidden="true" /> :
                 isFailed ? <XCircle size={14} aria-hidden="true" /> :
                 isSkipped ? <SkipForward size={14} aria-hidden="true" /> :
                 <FileCode size={14} aria-hidden="true" />}
                <span className="approval-file-name">{file.name}</span>

                {isPending && isActive && (
                  <span className="approval-file-acts">
                    {/* Yalnız ikon taşıyan bir butonun erişilebilir adı yoktu:
                        ekran okuyucuda adsız, testte de seçilemez durumdaydı —
                        bu yüzden "atla" yolu hiç ölçülememişti (2026-07-29). */}
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleSkip(i); }}
                      aria-label={t('approval.skip')}
                      title={t('approval.skip')}
                      className="icon-btn approval-file-skip"
                    >
                      <SkipForward size={14} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleAccept(i); }}
                      className="btn btn-ghost approval-file-apply"
                    >
                      {t('approval.apply')}
                    </button>
                  </span>
                )}

                {isPending && !isActive && (
                  <Eye size={14} className="approval-file-peek" aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ul>
      )}
      actions={(
        <>
          <button type="button" onClick={handleAcceptAll} disabled={processing} className="btn btn-primary">
            <CheckIcon />
            <span>{t('approval.approveAll')}</span>
          </button>
          {/* "İptal" bir KARARDIR — `onDone` ("Kapat") ile aynı şey değil. */}
          <button type="button" onClick={onCancel ?? onDone} className="btn btn-ghost">
            {t('approval.cancel')}
          </button>
        </>
      )}
    />
  );
};
