// Announced integrations (computer use, connectors, Blender). No flag is on, so the group is not drawn.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const INTEGRATIONS: GuideTopic[] = [
  {
    id: "computer-use", group: "integrations", since_version: "next", available_when: "feature.computerUse",
    title: T("Bilgisayarı kullanmak", "Computer use"),
    summary: T("Fareyi ve klavyeyi senin iznine göre ben kullanırım.", "I use the mouse and keyboard, with your permission."),
    steps: [
      { anchor: "computer-use-bar", side: "below", align: "center",
        title: T("Ekranında çalışırken", "While I work on your screen"),
        text: T("Bu şerit çalıştığımı gösterir; Esc ile her an durdurursun.", "This bar shows I am at work; Esc stops me at any time."),
      },
    ],
  },
  {
    id: "app-support", group: "integrations", since_version: "next", available_when: "integration.apps",
    title: T("Bağlayıcılar", "Connectors"),
    summary: T("Bir uygulamaya tek tuşla erişim ver; Claude ve Codex'teki bağlayıcılar gibi.", "Give access to an app with one click, like the connectors in Claude and Codex."),
    steps: [
      { anchor: "integration-switches", side: "below", align: "start",
        title: T("Bağlayıcılar", "Connectors"),
        text: T("Bir uygulamaya tek tuşla bağlan; her bağlayıcının Unity'deki gibi kendi anahtarı ve ışığı olur.", "Connect an app with one click; each connector gets its own switch and light, like Unity."),
      },
    ],
  },
  {
    id: "blender", group: "integrations", since_version: "next", available_when: "integration.blender",
    title: T("Blender", "Blender"),
    summary: T("Modeli Blender'da düzenleyip Unity'ye getiririm.", "I edit a model in Blender and bring it to Unity."),
    steps: [
      { anchor: "integration-switch-blender", side: "below", align: "start",
        title: T("Blender anahtarı", "The Blender switch"),
        text: T("Açıkken Blender'ı da sürerim; yaptığım modeli Unity projesine aktarırım.", "While on I drive Blender too, and export what I make into the Unity project."),
      },
    ],
  },
];
