// The right panel. `animation-preview` and `agent-browser` are announced features: hidden until their flag exists.
// Copy ported from the approved mockup (round 12b, maket/index.html GUIDE); REHBER-KAYITLARI.md
// section 8 is generated from the same array. Edit the copy here, not in the guide code.
import { T, type GuideTopic } from '../types';

export const WORKSPACE: GuideTopic[] = [
  {
    id: "workspace", group: "workspace", since_version: "baseline", available_when: "always",
    title: T("Çalışma alanı", "The workspace"),
    summary: T("Değiştirdiğim her şey sağdaki panelde.", "Everything I change shows up in the right panel."),
    keywords: T("panel sahne dosyalar kod önizleme", "panel scene files code preview"),
    steps: [
      { anchor: "workspace", side: "left", align: "center", prepare: ["workspace.peek"],
        title: T("Yaptığımı buradan izle", "Watch what I do here"),
        text: T("Sahne, Dosyalar, Kod, Önizleme: değiştirdiğim her şey bu panelde. Sohbette bir dosya adına tıklarsan burada açılır.", "Scene, Files, Code, Preview: everything I change shows up in this panel. Click a file name in the chat and it opens here."),
      },
    ],
  },
  {
    id: "ws-widths", group: "workspace", since_version: "baseline", available_when: "always",
    title: T("Panel genişliği", "Panel width"),
    summary: T("Dar, yarım, odak: sohbetle panel yeri paylaşır.", "Narrow, half, focus: chat and panel share the room."),
    keywords: T("genişlik dar yarım odak boyut", "width narrow half focus size"),
    steps: [
      { anchor: "ws-widths", side: "left", align: "start", prepare: ["screen:chat"],
        title: T("Panel ne kadar yer kaplasın", "How much room the panel takes"),
        text: T("Dar: sohbet ana sahne. Yarım: yan yana. Odak: panel geniş, sohbet ince bir şerit. Kenarı sürükleyerek de ayarlarsın; ← → sekmeler arasında gezer.", "Narrow: the chat leads. Half: side by side. Focus: the panel wide, the chat a strip. Drag the edge too; ← → moves between tabs."),
      },
    ],
  },
  {
    id: "code", group: "workspace", since_version: "baseline", available_when: "always",
    title: T("Kod ve değişiklikler", "Code and changes"),
    summary: T("Değişen satırlar, onay bekleyen değişiklik, açık dosyalar.", "Changed lines, a change waiting for you, open files."),
    keywords: T("diff editör satır kaydet c#", "diff editor line save c#"),
    steps: [
      { anchor: "ws-code", side: "left", align: "center", prepare: ["screen:chat", "workspace.width:half", "workspace.tab:code"],
        title: T("Neyi değiştirdiğimi gör", "See what I changed"),
        text: T("Değişen satırlar renkli görünür. Sekmedeki işaret onay bekleyen bir değişiklik demek; Kabul et ya da Reddet burada da var.", "Changed lines show in colour. The mark on the tab means a change is waiting for you; Accept and Reject are here too."),
      },
      { anchor: "ws-code-tabs", side: "left", align: "start",
        title: T("Açık dosyalar", "Open files"),
        text: T("Her dosya kendi sekmesinde. C# dosyalarında öneri ve tanıma gitme çalışır; Ctrl S kaydeder.", "Each file gets its own tab. C# files get suggestions and go-to-definition; Ctrl S saves."),
      },
    ],
  },
  {
    id: "preview", group: "workspace", since_version: "baseline", available_when: "always",
    title: T("Önizleme: resim ve 3D model", "Preview: images and 3D models"),
    summary: T("Bir varlığı Unity'ye geçmeden görmek.", "Look at an asset without switching to Unity."),
    keywords: T("fbx glb png model animasyon döndür", "fbx glb png model animation orbit"),
    steps: [
      { anchor: "ws-preview", side: "left", align: "center", prepare: ["screen:chat", "workspace.width:focus", "preview.open:model"],
        title: T("Önizleme", "Preview"),
        text: T("Dosyalar'da bir resme ya da 3D modele tıkla, burada açılır. Sürükleyerek döndür, tekerlekle yakınlaştır; modelin animasyonu varsa oynatırsın.", "Click an image or a 3D model under Files and it opens here. Drag to orbit, scroll to zoom; if the model has animations, play them."),
      },
    ],
  },
  {
    id: "changed-files", group: "workspace", since_version: "baseline", available_when: "project.git",
    title: T("Değişen dosyalar", "Changed files"),
    summary: T("Bu projede neye dokunduğumun listesi.", "A list of what I touched in this project."),
    keywords: T("git değişiklik yeni silinen", "git change new deleted"),
    steps: [
      { anchor: "changed-files", side: "left", align: "center", prepare: ["screen:chat", "workspace.tab:scene"],
        title: T("Değişen dosyalar", "Changed files"),
        text: T("Proje bir git deposuysa değişen, yeni ve silinen dosyalar burada; dosya ağacında da işaret taşırlar. Birine tıkla, farkını gör.", "If the project is a git repository, changed, new and deleted files are listed here and marked in the file tree. Click one to see the difference."),
      },
    ],
  },
  {
    id: "drawer", group: "workspace", since_version: "baseline", available_when: "always",
    title: T("Terminal ve sorunlar", "Terminal and problems"),
    summary: T("Alt çekmece: terminal, Unity konsolu, sorunlar.", "The bottom drawer: terminal, Unity console, problems."),
    keywords: T("terminal komut derleme hata uyarı çekmece", "terminal command build error warning drawer"),
    steps: [
      { anchor: "drawer", side: "left", align: "end", prepare: ["screen:chat", "workspace.width:half", "drawer:terminal"],
        title: T("Alt çekmece", "The bottom drawer"),
        text: T("Terminal, Unity konsolu ve sorunlar burada. Kapalıyken ince bir şerit olur ve son derlemenin özetini gösterir. Ctrl ` açıp kapatır.", "Terminal, Unity console and problems live here. Closed, it is a thin strip with the last build's summary. Ctrl ` opens and closes it."),
      },
      { anchor: "drawer", side: "left", align: "end", prepare: ["drawer:problems"],
        title: T("Sorunlar", "Problems"),
        text: T("C# uyarıları ve hataları satırıyla listelenir; birine tıklarsan dosya o satırda açılır.", "C# warnings and errors are listed with their line; click one and the file opens at that line."),
      },
    ],
  },
  {
    id: "animation-preview", group: "workspace", since_version: "next", available_when: "feature.animationPreview",
    title: T("Animasyon önizleme", "Animation preview"),
    summary: T("Unity animasyon kliplerini panelde oynat.", "Play Unity animation clips in the panel."),
    steps: [
      { anchor: "ws-preview-animation", side: "left", align: "center", prepare: ["screen:chat", "workspace.width:focus", "preview.open:animation"],
        title: T("Animasyonu burada oynat", "Play the animation here"),
        text: T("Bir .anim klibine tıkla: oynat, durdur, kare kare ilerle.", "Click an .anim clip: play, pause, step frame by frame."),
      },
    ],
  },
  {
    id: "agent-browser", group: "workspace", since_version: "next", available_when: "feature.agentBrowser",
    title: T("Ajan tarayıcısı", "Agent browser"),
    summary: T("Bir web sayfasını açıp okurken beni izle.", "Watch me open and read a web page."),
    steps: [
      { anchor: "ws-browser", side: "left", align: "center", prepare: ["screen:chat", "workspace.tab:browser"],
        title: T("Web'e bakarken izle", "Watch me browse"),
        text: T("Belge ya da örnek ararken açtığım sayfa bu sekmede görünür.", "The page I open while looking for docs or samples shows in this tab."),
      },
    ],
  },
];
