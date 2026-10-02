// The link to the Unity Editor. `new-project` waits for the Unity Hub button (a later port): its flag is off and its anchor is not in this build, so it is not listed.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const UNITY: GuideTopic[] = [
  {
    id: "unity-switch", group: "unity", since_version: "baseline", available_when: "always",
    title: T("Unity anahtarı", "The Unity switch"),
    summary: T("Açıkken Editor'ü ben sürerim; kapalıyken yalnız dosyalarla çalışırım.", "On, I drive the Editor; off, I only work with files."),
    keywords: T("mcp bağlantı editor", "mcp connection editor"),
    steps: [
      { anchor: "unity-switch", side: "below", align: "start",
        title: T("Unity anahtarı", "The Unity switch"),
        text: T("Açıkken Unity Editor'ü ben sürerim: sahne, nesne, prefab. Kapatırsan Unity'ye hiç dokunmam, yalnız dosyalarla çalışırım.", "While it's on I drive the Unity Editor: scenes, objects, prefabs. Turn it off and I don't touch Unity at all, I only work with files."),
      },
    ],
  },
  {
    id: "unity-states", group: "unity", since_version: "baseline", available_when: "always",
    title: T("Bağlantı ışıkları", "What the light means"),
    summary: T("Anahtar senin isteğin, ışık gerçek durum; bağlanmıyorsa ne yapılır.", "The switch is your wish, the light is the real state; what to do when it won't connect."),
    keywords: T("engellendi bağlanıyor neden port mcp", "blocked connecting why port mcp"),
    steps: [
      { anchor: "unity-light", side: "below", align: "start", prepare: ["screen:chat"],
        title: T("Işık ne diyor", "What the light says"),
        text: T("Anahtar senin isteğin, ışık gerçek durum: Kapalı, Bağlanıyor, Bağlı (proje ve sahneyle), Unity açık değil, Engellendi.", "The switch is what you asked for, the light is what is true: Off, Connecting, Connected (with project and scene), Unity not open, Blocked."),
      },
      { anchor: "settings-unity-switch", side: "below", align: "start", prepare: ["settings:unity"],
        title: T("Bağlanmıyorsa", "If it won't connect"),
        text: T("Engellendi derse yanındaki Neden? sebebini ve çözümünü söyler. Aynı anahtar ve sorun giderme burada, Ayarlar › Unity'de.", "If it says Blocked, the Why? next to it tells you the cause and the fix. The same switch and the troubleshooting live here, in Settings › Unity."),
      },
    ],
  },
  {
    id: "unity-console", group: "unity", since_version: "baseline", available_when: "always",
    title: T("Unity konsolu", "The Unity console"),
    summary: T("Editor'ün loglarını uygulamadan izlemek.", "Watching the Editor's logs from here."),
    keywords: T("log uyarı hata konsol", "log warning error console"),
    steps: [
      { anchor: "drawer", side: "left", align: "end", prepare: ["screen:chat", "workspace.width:half", "drawer:console"],
        title: T("Unity'nin konsolu", "Unity's console"),
        text: T("Unity'nin logları burada akar; uyarıları ve hataları süzersin. Unity bağlıyken dolar.", "Unity's logs stream in here; filter them down to warnings or errors. It fills while Unity is connected."),
      },
    ],
  },
  {
    id: "new-project", group: "unity", since_version: "next", available_when: "feature.unityHubNewProject",
    title: T("Yeni Unity projesi", "A new Unity project"),
    summary: T("Unity Hub'ı açar; projeyi orada oluşturup burada açarsın.", "Opens Unity Hub; create the project there, open it here."),
    keywords: T("hub proje oluştur klasör", "hub project create folder"),
    steps: [
      { anchor: "welcome-new-project", side: "right", align: "center", prepare: ["screen:welcome"],
        title: T("Yeni Unity projesi", "A new Unity project"),
        text: T("Bu düğme Unity Hub'ı açar. Projeyi orada oluşturursun, sonra klasörünü bu ekrandan seçersin.", "This button opens Unity Hub. Create the project there, then pick its folder on this screen."),
      },
    ],
  },
];
