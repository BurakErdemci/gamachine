// Remote control from the phone.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const PHONE: GuideTopic[] = [
  {
    id: "phone-pair", group: "phone", since_version: "baseline", available_when: "always",
    title: T("Telefonu eşleştir", "Pair your phone"),
    summary: T("Kodu telefonla okut, iki ekranda aynı kodu doğrula.", "Scan the code with your phone; check both screens show the same code."),
    keywords: T("uzaktan kontrol qr eşleştir", "remote control qr pair"),
    steps: [
      { anchor: "settings-remote", side: "below", align: "start", prepare: ["settings:remote"],
        title: T("Uzaktan kontrol", "Remote control"),
        text: T("Açınca telefonun bu bilgisayara bağlanabilir. Kapalıyken hiçbir şey bilgisayarından çıkmaz.", "Turn it on and your phone can connect to this computer. While it is off nothing leaves your computer."),
      },
      { anchor: "settings-remote-pair", side: "left", align: "center",
        title: T("Telefonu eşleştir", "Pair the phone"),
        text: T("Telefonun kamerasıyla kodu okut. İki ekranda aynı kod görünürse onayla; başka bir şey görürsen onaylama.", "Scan the code with the phone camera. Approve only if both screens show the same code."),
      },
    ],
  },
  {
    id: "phone-approve", group: "phone", since_version: "baseline", available_when: "always",
    title: T("Telefondan onay", "Approving from the phone"),
    summary: T("Kartlar cebine gelir; kararın her yere yansır.", "Cards come to your pocket; your decision shows everywhere."),
    keywords: T("telefon bildirim onay cep", "phone notification approve pocket"),
    steps: [
      { anchor: "approval-phone-hint", side: "above", align: "end", prepare: ["screen:chat"],
        title: T("Onayı cebinden ver", "Approve from your pocket"),
        text: T("Eşleşmiş telefona onay kartları düşer; oradan verdiğin karar burada da anında görünür.", "Approval cards reach your paired phone; a decision made there shows here at once."),
      },
      { anchor: "phone-status", side: "right", align: "end",
        title: T("Telefon bağlı", "Phone connected"),
        text: T("Bu nokta telefonun bağlı olduğunu söyler. Telefondan mesaj yazar, modeli, düşünmeyi ve onay modunu da değiştirirsin.", "This dot says the phone is connected. From the phone you can write, and change the model, thinking and approval mode."),
      },
    ],
  },
];
