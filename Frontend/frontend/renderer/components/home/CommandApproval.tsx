import React from 'react';
import { useLang } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';
import { RiskReasonLine } from './RiskReasonLine';
import { ApprovalCard, ApprovalWhy, CheckIcon } from './ApprovalCard';

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
  /** A paired phone can decide this card too. */
  phonePaired?: boolean;
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
    name: 'card.nameShell',
  },
  unity: {
    title: 'unityApproval.title',
    confirm: 'unityApproval.confirm',
    run: 'unityApproval.run',
    warning: 'unityApproval.warning',
    name: 'card.nameUnity',
  },
  // A note from one chat's AI to another chat (`send_chat_message`). The
  // first line of `command` names both chats, the rest is the note itself.
  mail: {
    title: 'mailApproval.title',
    confirm: 'mailApproval.confirm',
    run: 'mailApproval.run',
    warning: 'mailApproval.warning',
    name: 'card.nameMail',
  },
} as const;

export const CommandApproval: React.FC<CommandApprovalProps> = ({ command, onConfirm, onCancel, kind = 'shell', riskReason, riskDetail, phonePaired }) => {
  const { t } = useLang();
  const unity = kind === 'unity';
  const mail = kind === 'mail';
  const m = METIN[kind] ?? METIN.shell;
  const breakAt = command.indexOf('\n');
  const mailRoute = mail ? (breakAt < 0 ? command : command.slice(0, breakAt)) : '';
  const mailBody = mail && breakAt >= 0 ? command.slice(breakAt + 1).replace(/^\n+/, '') : '';
  return (
    <ApprovalCard
      kind={kind}
      testId="command-approval"
      who={t(m.title)}
      name={t(m.name)}
      sentence={t(m.confirm)}
      // The balanced-mode reason when there is one; otherwise the card's own warning.
      why={riskReason ? <RiskReasonLine reason={riskReason} detail={riskDetail} /> : <ApprovalWhy>{t(m.warning)}</ApprovalWhy>}
      // What is approved stays VISIBLE, not folded behind "Details" as in the mockup: a gate
      // that shows something other than what it approves is no gate (see the bidi note below).
      // The body scrolls inside itself and keeps line breaks (K9: the card also shows a file's
      // content to be written); without `max-height` a long body pushed the buttons off screen.
      body={mail ? (
        <div data-testid="mail-card" className="approval-cmd approval-mail custom-scrollbar">
          <p data-testid="mail-route" className="approval-mail-route">{stripBidi(mailRoute)}</p>
          <p data-testid="mail-body" className="approval-mail-body">{stripBidi(mailBody)}</p>
        </div>
      ) : (
        <pre className="approval-cmd custom-scrollbar"><code>
          {!unity && <span className="approval-cmd-prompt" aria-hidden="true">$</span>}
          {/* Yalnız GÖSTERİM temizleniyor; `onConfirm` hâlâ gerçek komutu
              çalıştırıyor. Sanitize edilmiş metni geri göndermek, sunucuya
              kullanıcının onayladığından BAŞKA bir komut yollamak olurdu.
              Temizlik burada zorunlu, çünkü U+202E React'in kaçırdığı
              türden değil: markup değil, çizim yönü. Tarayıcı onu
              onurlandırınca `printf safe<U+202E>; rm -rf /` ekranda zararsız
              görünüp zararlı olanı onaylatır — gösterdiğinden başkasını
              onaylayan bir kapı, kapı değildir. */}
          {stripBidi(command)}
        </code></pre>
      )}
      phoneHint={phonePaired}
      actions={(
        <>
          <button type="button" onClick={onConfirm} className="btn btn-primary">
            <CheckIcon />
            <span>{t(m.run)}</span>
          </button>
          <button type="button" onClick={onCancel} className="btn btn-ghost">
            {t('cmdApproval.cancel')}
          </button>
        </>
      )}
    />
  );
};
