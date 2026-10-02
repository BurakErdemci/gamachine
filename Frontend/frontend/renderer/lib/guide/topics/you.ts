// Profile and looks.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const YOU: GuideTopic[] = [
  {
    id: "profile", group: "you", since_version: "baseline", available_when: "always",
    title: T("Yapımcı profili", "Maker profile"),
    summary: T("Biten görevler, seri günler, başarımlar.", "Finished tasks, streaks, achievements."),
    keywords: T("profil seviye xp başarım istatistik", "profile level xp achievement stats"),
    steps: [
      { anchor: "side-profile", side: "right", align: "end", prepare: ["screen:chat"],
        title: T("Emeğin burada birikir", "Your work adds up here"),
        text: T("Biten görevler, onayladığın kartlar, seri günlerin ve başarımların. Hepsi bu bilgisayarda kalır.", "Finished tasks, cards you approved, your streaks and achievements. It all stays on this computer."),
      },
      { anchor: "profile-shelf", side: "above", align: "start", prepare: ["screen:profile"],
        title: T("Başarım rafı", "The achievement shelf"),
        text: T("Kilitli başarımlar ne kadar kaldığını gösterir. Adını profilin başlığındaki kalemle değiştirirsin.", "Locked achievements show how far you are. Change your name with the pencil in the profile header."),
      },
    ],
  },
  {
    id: "themes", group: "you", since_version: "baseline", available_when: "always",
    title: T("Tema, yazı tipi, boyut", "Theme, font, size"),
    summary: T("Dört tema; okuma ve kod yazı tipi; metin boyutu.", "Four themes; reading and code fonts; text size."),
    keywords: T("tema görünüm font yazı tipi boyut arena sade pafta atölye", "theme look font size arena plain blueprint workshop"),
    steps: [
      { anchor: "settings-themes", side: "below", align: "start", prepare: ["settings:appearance"],
        title: T("Dört kıyafet", "Four outfits"),
        text: T("Arena, Sade, Pafta, Atölye: aynı uygulama, dört görünüm. Tema bazı kelimeleri de değiştirir: Arena'da “Görev”, Pafta'da “Revizyon”.", "Arena, Plain, Blueprint, Workshop: one app, four looks. A theme swaps a few words too: “Quest” in Arena, “Revision” in Blueprint."),
      },
      { anchor: "settings-fonts", side: "above", align: "start",
        title: T("Yazı tipi ve boyut", "Font and size"),
        text: T("Okuma ve kod yazı tipini seçersin. Metin boyutu bütün pencereyi büyütür.", "Pick the reading and code fonts. Text size scales the whole window."),
      },
    ],
  },
];
