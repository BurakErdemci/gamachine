import React from 'react';
import { motion } from 'framer-motion';
import { Terminal, X, Check, AlertTriangle, Box, Mail } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { RiskReasonLine } from './RiskReasonLine';

interface CommandApprovalProps {
  command: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Neyin onaylandığı. Metni ve ikonu bu seçiyor.
   *
   * Ayrım güvenlik açısından zorunlu: aynı kartı Unity araç çağrıları için
   * "Terminal Komutu Onayı" başlığıyla göstermek, kullanıcıya onayladığı şeyi
   * YANLIŞ söylerdi. Onaylanan bir kabuk komutu değil, Unity projesini
   * değiştiren bir araç çağrısı. Kablolama (onay/ret, gate kimliği) ortak
   * kalıyor — ayrışan yalnız kullanıcının okuduğu şey. */
  kind?: 'shell' | 'unity' | 'mail';
  /** Why balanced mode stopped here; absent for auto/step cards. */
  riskReason?: string;
  riskDetail?: string;
}

/** Metin anahtarları BİRLİK tipinde tutuluyor: `t` yalnız bilinen anahtarları
 *  kabul ediyor, yani `t(\`${ns}.title\`)` gibi bir şablon dizge derlenmezdi.
 *  Tablo aynı zamanda eksik çeviriyi derleme anında yakalıyor. */
const METIN = {
  shell: {
    title: 'cmdApproval.title',
    confirm: 'cmdApproval.confirm',
    run: 'cmdApproval.run',
    warning: 'cmdApproval.warning',
  },
  unity: {
    title: 'unityApproval.title',
    confirm: 'unityApproval.confirm',
    run: 'unityApproval.run',
    warning: 'unityApproval.warning',
  },
  // A note from one chat's AI to another chat (`send_chat_message`). The
  // first line of `command` names both chats, the rest is the note itself.
  mail: {
    title: 'mailApproval.title',
    confirm: 'mailApproval.confirm',
    run: 'mailApproval.run',
    warning: 'mailApproval.warning',
  },
} as const;

export const CommandApproval: React.FC<CommandApprovalProps> = ({ command, onConfirm, onCancel, kind = 'shell', riskReason, riskDetail }) => {
  const { t } = useLang();
  const unity = kind === 'unity';
  const mail = kind === 'mail';
  const m = METIN[kind] ?? METIN.shell;
  const Ikon = mail ? Mail : unity ? Box : Terminal;
  const breakAt = command.indexOf('\n');
  const mailRoute = mail ? (breakAt < 0 ? command : command.slice(0, breakAt)) : '';
  const mailBody = mail && breakAt >= 0 ? command.slice(breakAt + 1).replace(/^\n+/, '') : '';
  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className="mt-3 rounded-xl border border-amber-500/30 bg-amber-950/10 overflow-hidden shadow-lg shadow-amber-950/20"
    >
      <div className="flex items-center justify-between px-4 py-2.5 bg-amber-500/10 border-b border-amber-500/20">
        <div className="flex items-center gap-2 text-amber-400 font-bold text-[11px] uppercase tracking-wider">
          <AlertTriangle size={14} />
          {t(m.title)}
        </div>
        <button onClick={onCancel} className="text-slate-500 hover:text-white transition-colors">
          <X size={14} />
        </button>
      </div>

      <div className="p-4">
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-amber-500/20 flex items-center justify-center shrink-0">
            <Ikon size={20} className="text-amber-400" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[13px] text-white font-medium leading-tight mb-2">
              {t(m.confirm)}
            </p>
            <RiskReasonLine reason={riskReason} detail={riskDetail} className="mb-2" />
            {/* Gövde KENDİ İÇİNDE kaydırılıyor ve satır sonları korunuyor.
              *
              * İkisi de K9'un gereği: kart artık yalnız bir yol değil, yazılacak
              * dosyanın içeriğini de gösteriyor. `whitespace-pre-wrap` olmadan
              * çok satırlı gövde tek satıra çöküyordu (kod okunamaz hale gelir),
              * `max-h` olmadan ise uzun bir içerik ONAY/RET düğmelerini ekranın
              * altına itiyordu — kullanıcının kararını göstermeyen bir onay
              * kartı, kartın var olma sebebini ortadan kaldırır. */}
            {mail ? (
              <div data-testid="mail-card" className="bg-black/50 rounded-lg px-3 py-2 border border-white/5 max-h-[320px] overflow-y-auto custom-scrollbar">
                <p data-testid="mail-route" className="text-[11px] text-amber-300 font-semibold mb-1.5 break-all">{stripBidi(mailRoute)}</p>
                <p data-testid="mail-body" className="text-[12px] text-slate-200 whitespace-pre-wrap break-words leading-relaxed">{stripBidi(mailBody)}</p>
              </div>
            ) : (
            <div className="bg-black/50 rounded-lg px-3 py-2 border border-white/5 max-h-[320px] overflow-y-auto custom-scrollbar">
              <code className="block text-[11px] text-emerald-400 font-mono break-all whitespace-pre-wrap leading-relaxed">
                {!unity && <span className="text-slate-600 mr-1.5 select-none">$</span>}
                {/* Yalnız GÖSTERİM temizleniyor; `onConfirm` hâlâ gerçek komutu
                    çalıştırıyor. Sanitize edilmiş metni geri göndermek, sunucuya
                    kullanıcının onayladığından BAŞKA bir komut yollamak olurdu.
                    Temizlik burada zorunlu, çünkü U+202E React'in kaçırdığı
                    türden değil: markup değil, çizim yönü. Tarayıcı onu
                    onurlandırınca `printf safe<U+202E>; rm -rf /` ekranda zararsız
                    görünüp zararlı olanı onaylatır — gösterdiğinden başkasını
                    onaylayan bir kapı, kapı değildir. */}
                {stripBidi(command)}
              </code>
            </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onConfirm}
            className="flex-1 flex items-center justify-center gap-2 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-[12px] font-bold transition-all shadow-lg shadow-amber-900/20 active:scale-[0.98]"
          >
            <Check size={14} className="stroke-[3px]" />
            {t(m.run)}
          </button>
          <button
            onClick={onCancel}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-[12px] font-bold transition-all"
          >
            {t('cmdApproval.cancel')}
          </button>
        </div>
      </div>

      <div className="px-4 py-2 bg-black/40 border-t border-amber-500/10">
        <p className="text-[10px] text-slate-600 italic">
          * {t(m.warning)}
        </p>
      </div>
    </motion.div>
  );
};
