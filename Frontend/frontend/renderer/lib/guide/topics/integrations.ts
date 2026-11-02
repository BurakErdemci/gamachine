// Announced integrations (computer use, connectors, Blender). No flag is on, so the group is not drawn.
// Computer use already points at the live tab and the floating indicator; its flag stays off until
// guide-flow.test.tsx and guide-registry.test.ts are updated to list the integrations group.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const INTEGRATIONS: GuideTopic[] = [
  {
    id: "computer-use", group: "integrations", since_version: "next", available_when: "feature.computerUse",
    title: T("Bilgisayarı kullanmak", "Computer use"),
    summary: T("Fareyi ve klavyeyi senin iznine göre ben kullanırım.", "I use the mouse and keyboard, with your permission."),
    steps: [
      { anchor: "ws-tabs", side: "below", align: "end",
        title: T("Ekranında çalışırken", "While I work on your screen"),
        text: T("Çalışırken ekranın köşesinde küçük bir etiket süreyi gösterir; sürdüğüm pencereyi Bilgisayar sekmesinde canlı izlersin. Durdur ya da ⌘⇧Esc beni bu tur için durdurur.",
          "While I work, a small tag in a corner of the screen shows the time; you watch the window I drive, live, in the Computer tab. Stop or ⌘⇧Esc halts me for this turn."),
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
    summary: T("Blender'ı açıp modeli orada yapar ve düzenlerim.", "I open Blender and build or edit the model there."),
    steps: [
      { anchor: "integration-switch-blender", side: "below", align: "start",
        title: T("Blender anahtarı", "The Blender switch"),
        text: T("Açıkken Blender'ı kendim açıp kullanırım: sahneyi okur, görüntüsüne bakar, kod çalıştırırım.", "While on I open and drive Blender myself: I read the scene, look at it and run code in it."),
      },
    ],
  },
];
