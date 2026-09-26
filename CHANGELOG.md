# Değişiklik günlüğü

Bu dosya kullanıcıya görünen değişiklikleri taşır. Tam geçmiş için `git log`.

> ℹ️ **8 Ağu 2026'dan itibaren yeni girdiler İngilizce yazılıyor.** Sebep: bu
> dosyanın en üst bölümü `RELEASE_NOTES.md`'ye taşınıyor ve orası yabancı bir
> kullanıcının indirme anında gördüğü sayfa. Eski girdiler olduğu gibi bırakıldı —
> geçmişi yarım çevirmek, tek dilde bırakmaktan da iki dilde bırakmaktan da kötü.

## Unreleased

### "Compiled clean" now means it compiled

An empty Unity console used to pass for a clean compile, but the console reads
0 errors before and while Unity compiles. The Editor plugin now tracks every
script compile, and a new read-only `compile_status` tool reports one verdict:
errors, clean, compiling, pending, stale, timeout or unknown. "Clean" means the
code as it is now compiles and the domain reload after it has finished. The
script tools wait for the compile by default and return the verdict with the
write, `refresh_unity` returns it too, and `read_console` says when its list is
not final yet.

### Agents can no longer break asset GUIDs

Inside a Unity project, writing, deleting or moving a `.meta` file, and writing a
scene, prefab, material or other Unity YAML asset as raw text, is now refused on
every path and in both step and auto mode. It is a fixed rule rather than an
approval card: the agent is told to use the Unity MCP tools, which make Unity
write the file and keep its `.meta` with it. Shell commands are checked by a
heuristic, and `execute_code` / `execute_menu_item` cannot be checked; see
docs/security.md.

## v3.2.0 — 6 September 2026

### New models: Gemini 3.8 Flash, Gemini 3.7 Flash and GPT-6 Astra

Gemini 3.8 Flash is the recommended default now, with 3.7 Flash alongside it,
and GPT-6 Astra joins the Codex list. The reasoning-effort choices differ per
model — GPT-6 offers low through max, the new Gemini models low, medium and
high — and the picker only shows what the model you picked actually supports.

Gemini 3.7 and 3.8 need agy 1.1.25 or newer for their display names. One
limitation worth knowing before you pick it: GPT-6 cannot be used on the older
chat-completions agent loop, because its tool calling exists only on the newer
API. Choosing it there now says so instead of failing halfway through a run.

### The conversation wakes itself up when background work finishes

A run used to be tied to one open connection. If a subagent or a background
command finished after that connection closed, nothing resumed the turn — the
conversation just sat there until you typed something, and the result of the
work you were waiting for was invisible.

It now wakes itself. When background work completes, the conversation picks the
turn back up and tells you what finished. It waits if an approval card or a
prompt is on screen, so a wake never lands on top of a decision you are in the
middle of making, and it stops after three wakes in a row so a chain of tasks
cannot talk to itself indefinitely.

### Antigravity CLI keeps one session open instead of restarting every turn

The Antigravity path used to start a fresh process for every single turn and
hand it the whole conversation as a command-line argument. That capped how much
history could be carried on Windows, made each turn pay a full startup, and left
the app scraping the CLI's own transcript files to figure out what happened.

It now holds one live session per conversation and sends each turn into it. The
turn's real token usage comes back from the CLI itself rather than being
reconstructed, the Windows length limit is gone, and everything that existed to
work around the restart — the history trimming, the transcript scraping, the
polling loop that guessed whether the CLI was still alive — is gone with it.

### The usage and context panel stops going blank

The panel emptied out whenever the CLI session was busy or unavailable, and the
context section never showed anything at all. Usage readings are account-level
and stay true for a while, so the last successful reading is now kept for two
hours and shown with a "stale" badge and its age when nothing live can be
reached. Context falls back to an estimate from the stored conversation, clearly
labelled as an estimate rather than presented as a measurement.

### Removed: the token and cost readout in the message box

The small "— tok" figure next to the memory ring reported wildly wrong numbers.
It is removed rather than corrected. The memory ring and the usage button are
unchanged.

### Under the hood: secrets, approval cards and session cleanup

Everything the Antigravity CLI sends back now passes through one redaction step
on its way to the screen, including the final answer of a successful turn, which
previously bypassed it — that was the path by which a credential in a model's
own reply could reach the interface. Key names are masked as well as values, and
a deeply nested payload is trimmed rather than being allowed to kill the turn.

Approval cards are attributed to the conversation that created them, so a
pending card in one conversation no longer blocks another one from continuing.
A card that cannot be attributed still blocks, which is the safe direction.
Closing a session releases its own pending cards immediately instead of leaving
them to expire, and Stop only dismisses the cards belonging to the conversation
being stopped.

## v3.1.0 — 3 September 2026

### Dictation: speak into the chat box, and it never leaves the machine

There is a microphone button in the chat box now. Press it and talk, and the
words appear in the box while you are still speaking; stop the mic, read what
came out, fix anything that is wrong and press Enter. Nothing is sent for you.
The language toggle starts on whatever language the app is set to, there is a
running timer, and recording stops on its own after 60 seconds.

Recognition happens here, on your computer. Turkish and English speech models
ship inside the installer, so dictation works with the network unplugged and no
audio is uploaded anywhere. The price is download size: the two models add
roughly 176 MB to the installer.

The first version of this pasted the whole sentence in at once when you stopped.
Speaking into the box live came later the same day, after using it — the text
should arrive as you talk, the way the Claude desktop app does it.

### Hardened before release

An external audit of the dictation code found 32 issues; 29 were fixed. Most
were races and cleanup gaps: the recording could lose its last words if a
request failed while stopping, a stray final result could land after an error
had already been shown, and concurrent audio chunks could exceed the session's
size budget. Each of these now has a regression test.

### 3D model files open in a preview instead of the code editor

Clicking an `.fbx` in the file tree used to hand it to the text editor, which
had nothing to say about it. It now opens in a preview panel in the same content
area: orbit the camera with the mouse, and if the file carries an animation, play
it from a transport bar with a timeline you can scrub and speeds of 0.25x, 0.5x,
1x and 2x.

Six more file types (.glb, .gltf, .dae, .obj, .stl, .ply) read alongside FBX. A
`.gltf` that keeps its data in a sibling `.bin` or texture file cannot be
drawn here and says so rather than failing as a broken download; `.blend` and
`.3ds` are refused before the file is even read, because nothing in this app can
open them.

Animation-only files get a body to look at. An FBX exported "without skin" is
bones and clips and not a single visible surface — measured on a bought clip
pack: 0 meshes, 65 bones, one 0.97-second clip — so the panel used to say there
was nothing in the file. It now builds a mannequin out of the file's own bones
and animates that, and it works whether the rig was authored in centimetres or
metres. A rig with an unreasonable number of joints is refused with a message
saying so instead of freezing the window: building the figure takes 104 ms at
1,000 joints, 1,171 ms at 20,000 and 3,531 ms at 60,000, and the file carrying
20,000 of them is only 682 KB, so no size limit was ever going to catch it.

Every way a preview can fail now has its own sentence — over the 64 MiB size
limit, a type nothing here can read, a half-complete glTF, a file that parses to
nothing visible — where previously all of them read as "this model could not be
opened". If the graphics context is lost, the panel says it is reconnecting
rather than going quietly dark.

### Images open in the content area too

Clicking a texture or a sprite in the file tree shows the picture, in the same
slot the 3D preview uses. Actual-size mode draws pixel art sharp instead of
letting the browser smooth a 32x32 icon into a blur. Images up to 32 MiB open;
`.tga`, `.psd`, `.exr` and `.tif` are not among the types that can be shown.

### Video links work without installing anything first

Pasting a YouTube link into the chat quietly did nothing on most machines: the
two programs that fetch and read video were expected to be installed and on the
system PATH, and they usually were not. Both now ship with the app.

Two other things about that path were wrong. A YouTube Shorts link failed
entirely because the sound track was being downloaded and then thrown away —
nothing here listens to audio, frames come from the picture and the text comes
from the subtitles — and when YouTube refused the sound, the whole video was
lost. Dropping it fixed the failing links and halves what is downloaded.

And when a download does fail, the message now says which failure it was. A
refusal, a private or removed video and a region block each say so, instead of
one sentence blaming "a dead link, geo-block or firewall" for whichever of the
three it was. Downloads also have a size and time budget now, and pressing Stop
actually stops the download rather than leaving it running in the background.

### The model list comes from your own account

The list of cloud models was forty lines maintained by hand, and it had drifted:
it was still offering Groq's `llama-3.3-70b-versatile`, which Groq shut down on
16 Aug 2026. That list is deleted.

Each provider is now asked, with your key, what your account can actually reach,
and that answer is the list. A second, public catalogue supplies the description
beside each name — what it is called, how big its context is, what it costs — and
that one needs no key at all, so the labels are there before you have configured
anything (measured 30 Aug 2026: 396 models, no credentials). A provider you have
no key for does not disappear from the picker; its models are shown as
unconfirmed, because "this model exists" and "your account has it" are two
different claims.

The suggestion chips in the settings dialog were a second copy of the same
hand-written list, found in a running build still recommending the dead Groq
model. They come from the live list now.

The subscription CLIs — Claude Code, Kimi, agy — keep hand-written lists: none of
them has a command that lists models, checked by running them.
Claude Fable 5.1 (`claude-fable-5-1`) is on the Claude Code list now, next to Fable 5:
both ids answer as themselves when run through the installed CLI (measured 3 Sep 2026).

### A usage and context panel, and a gauge that is always there

The context gauge only appeared after a turn had finished, which is the opposite
of what an indicator is for. It is now on screen whenever a conversation is open,
and says "no data yet" until there is data — an empty ring reads as zero percent,
and those are different claims. Beside the estimate sits the one number that is
actually measured: the real tokens this session has spent, and the cost when the
provider reported one. A path that reports nothing shows a dash rather than a
zero.

Usage and context reports used to be readable only by typing `/usage` or
`/context` as a chat message, which left a question and an answer in the history
every time you looked, and could not be done at all while a turn was running.
There is a button beside the gauge now; the report opens in a panel and nothing
is written into the conversation. When the real context figure arrives the gauge
stops estimating and drops the "~".

### Questions from the assistant can take more than one answer

When the assistant asks you a question with a list of options, you can now pick
several, type your own answer, or skip the question. Previously it was one option
or nothing — and the tool that asks these questions deliberately leaves "None" and
"Other" out of its options, on the understanding that a skip button and a text box
exist. They did not.

The card also no longer carries an answer over from the previous question. Two
questions in a row would inherit the first one's selection, with Send already
enabled, which could submit an answer nobody chose.

### A run that stops early says why

The agent used to stop after 15 tool calls and finish in a way that was
byte-identical to finishing normally, so a half-done task looked done. Real MCP
work runs fifty to sixty tool calls, reported from a running build and confirmed
by a test: a plain sixty-step run ended as "hit the limit".

What ends a run now is a lack of progress rather than a step count — the same
tool returning the same answer over and over — and the notice says which tool
repeated, instead of "I stopped making progress". The step ceiling still exists as
a last resort and is 300. Stopping a turn yourself now always ends it: pressing
Stop could previously hang a Codex turn with no way out, and the work already
streamed to the screen is kept rather than discarded.

### Errors say what actually went wrong

A failing provider used to be reported as the model refusing to answer, which
sends you off to rewrite a prompt when the answer is to wait — and an outage and
a rate limit were given the same wording, erasing the one distinction that
matters. Each now says what it was, and a model that cannot use tools at all says
that instead of putting the provider's raw JSON on screen.

Long silences are broken too. A slow provider could sit for two minutes with the
screen showing only "thinking", with nothing to say whether the app or the
provider was stuck; the SDK's own patience runs to ten minutes. Every fifteen
seconds the app now reports who it is waiting on, for how long, and that Stop
cancels it. The waits between retries announce themselves as well.

Error messages from the backend used to be fixed Turkish sentences, so an English
interface showed Turkish text. They are translated now.

### Gemini models can use tools

A run on a Gemini API model died on its first tool call with `Role 'tool' is not
supported`. That is another provider's convention; Google's own SDK sends tool
results a different way. The line had been wrong since 30 May 2026, so the
tool-using half of that provider had never worked in the product.

### The chat stays where you left it

Scrolling up to read something older meant being dragged back to the bottom on
every new chunk of output. It no longer moves the view unless you were already at
the bottom. Cards that need an answer — approval, questions, delete confirmations
— still scroll themselves into view, because a request you cannot see is a request
nobody can answer.

### The content area follows the file

Renaming, deleting, moving or dragging a file left the preview or the editor
sitting on a path that no longer named anything. Six separate operations had this
same problem and each was fixed one at a time; they all now go through one rule.
The panel follows a rename, empties itself when the file is gone, and clears when
you switch to another project or a project turns out to be missing. Renaming an
image to a `.cs` file now switches the content area from the picture to the
editor, and back the other way.

Underneath that, the app now recognises when two spellings mean the same file —
a relative and an absolute path, either slash on Windows, a different case,
`Assets/../hero.fbx` against `hero.fbx` — which is what let a deleted file stay
on screen in the first place.

### The Docker development container actually runs

Docker mode had been in the tree since the web era and had never worked. Four
independent reasons, measured on 31 Aug 2026: the app and the container held
different secrets and so every request was rejected; the container's port was
unreachable from outside; the image carried the wrong Python version; and nothing
was mounted, so the file tools would have worked on an empty image and reported
success. It runs now, and it refuses to start with a clear message rather than
failing silently.

The mode's limits are written down rather than left to be discovered: it is for
working on the backend with a cloud API model, and the subscription CLIs cannot
work inside it because they are installed on the host and signed in as the host
user. If a container is left running against an older project folder, startup
notices the mismatch and says so instead of quietly reading and writing the wrong
project.

### The project is now plain MIT

The Commons Clause condition has been removed; `LICENSE` is now the MIT licence
alone. The project is therefore open source in the OSI sense, and the READMEs say
so instead of "source-available".

Why the change: Commons Clause bought a signal, not enforcement — it does not stop
a determined closed fork, while it costs the OSI label, keeps the repository out of
package directories, deters contributors, and (unintentionally) prohibited paid
third-party support, which was never the intent. Selling a fork of the application
is now permitted. Already-released versions keep the terms they shipped with.

`unity-mcp/` remains MIT (c) 2025 CoplayDev under its own `LICENSE`, and the
third-party notices are unchanged.

### Under the hood

The bundled Unity sprite tool was brought up to date with its upstream project,
which also fixed animations whose blend thresholds Unity was discarding and an
"add to scene" step that reported success while attaching an animator to an
object with nothing to animate. The README was split so the engineering notes
start near the top rather than at 77% scroll depth, the reference material moving
into `docs/`. The rest of the release is the usual invisible half: a long series
of audits on file reading, the approval gates and the Docker path, each one
turned into a test that goes red without its fix.

## v3.0.3 — 9 Ağustos 2026

### Compact now actually shrinks the context

Compact summarised the chat and left the model's context exactly where it was.
You could press it, watch the conversation collapse into a summary, ask the CLI
how much context it was holding, and get the same number back — 773k of 1M
tokens, unchanged. Measured live, not inferred.

Two things were wrong, and the first one hid the second.

**The session was being revived.** Compact closed the live CLI session, which
used to be enough. Since 3.0.1 the app also remembers each chat's CLI session id
so it can resume where you left off — and nothing dropped that id on compact. The
next message resumed the old session, the CLI reloaded its full transcript from
disk, and the session we had just closed came back exactly as it was. Worse, on a
resumed session the freshly written summary is deliberately *not* injected, so
the old context returned and the new summary never arrived.

**Short chats skipped the reset entirely.** Compact returned early when the chat
had six messages or fewer — reasonable for summarising, wrong for resetting. After
one compact the stored chat *is* short, while the CLI session is still full. So
the second press reported "chat is already short" and reset nothing, which is the
state most people would hit.

Compact now drops the session id in both paths, so a short chat still gets a clean
CLI session. The message you get in that case says what actually happened, and it
now comes from the dictionary — English UI no longer shows a Turkish toast here.

### Windows installer name is fixed at the source

The installer has shipped as `Gamachine.Setup.X.Y.Z.exe` for three releases while
the update feed asked for `Gamachine-Setup-X.Y.Z.exe`, and the gap was closed by
hand every time. The dots come from NSIS's default name template, not from the
product name. The template is now pinned, so the build and the update feed agree
without anyone remembering to intervene.

## v3.0.2 — 9 Ağustos 2026

### The agent can now play a game it is not looking at

`manage_input` — the tool that hands your game to the agent — worked in demos
and failed in real use. The difference was never the input path. While you are
in the chat window Unity sits fully unfocused, and two separate things happen
there. Both were measured live against a running editor, with the window in the
background the whole time.

**The engine stops.** Play mode enters, `timeScale` reads 1, and the game sits
frozen at frame 2. Every key we sent was correct and no frame ever processed it.
The project's own *Run In Background* setting was already on and did not help —
the runtime value is separate and starts off.

**The devices get switched off.** The Input System's default behaviour disables
every device when the application loses focus, the real keyboard and mouse
included. The key arrives at a device nobody is listening to.

Both are now handled on the input path, so pressing Play by hand does not
quietly change how your editor behaves. Nothing is written to your project: the
settings we take over are handed back when play mode exits.

`describe` now reports whether the game is actually running. It used to answer
"can input reach the game" by listing resolved members and said yes while the
engine was frozen — and no focus flag can catch that, because during the freeze
Unity still reports itself as focused. It now returns a frame counter: call it
twice and compare.

### Documentation honesty

- The project is described as **source-available**, not open-source. It is
  MIT + Commons Clause, which restricts commercial use and therefore is not
  open-source under the OSI definition. The licence section was already correct;
  a one-line summary elsewhere contradicted it.
- Our own clarifying paragraph in `LICENSE` moved out of the Commons Clause text
  and is now labelled as a clarification, so the standard condition reads
  verbatim.
- The `~/.unity_architect_ai` paths now state why the old name survives: they are
  the address of your existing encryption key and database, and renaming them
  would make already-saved API keys undecryptable.

### New demo

The README recording is current again — one prompt builds a course in Unity, a
second hands the game to the agent, and the player cube walks and jumps its way
to the top step. 8.16 MB → 1.12 MB.

## v3.0.1 — 8 Ağustos 2026

Three fixes on top of v3.0.0; the first one blocked the Claude path entirely on
most Windows installs.

### 🔧 Claude Code would not start on Windows
- v3.0.0 stopped bundling the CLI, so the SDK falls back to PATH — where npm puts
  only a `claude.cmd` shim, and the SDK **refuses** `.cmd` (cmd.exe can execute
  commands injected through arguments). Session never opened:
  `Refusing to execute batch script '...\claude.CMD'`.
- The shim carries its target in plain text; the app now reads it and passes the
  real executable as `ClaudeAgentOptions(cli_path=)`. Measured here: resolves to
  `claude.exe` 2.1.226, which PATH never exposed.
- ⚠️ Recorded: the chat gate did **not** catch this. It asks "is a claude binary
  installed" and the shim answers yes; the SDK needs something it can *execute*.
  Two different questions with one name.

### 🧠 Session continuity
- **`resume`**: the CLI's own session is resumed when possible. It keeps the full
  transcript on disk; only the id was missing. Stored per (conversation, CLI
  family) with the workspace — a mismatch is ignored, because CLI sessions are
  keyed by project directory and resuming elsewhere would show **another
  project's** history.
- When a summary is still needed (agent switch, session gone), **the cut is now
  marked**. Previously the budget filled and older messages were dropped with a
  bare `break`; the model got a transcript starting mid-conversation and could not
  know. Measured: 17 of 48 messages survived, **71% of characters lost, silently**.
- Resuming does **not** also inject the transcript — that would show the model the
  same conversation twice.

### 🧪 Gates
backend **1127** · frontend **406** · tsc **0**

## v3.0.0 — 8 Ağustos 2026

**Unity Architect AI → Gamachine.** Major version, because this is not an upgrade
installed over the old one, and two behaviours changed deliberately.

### 🏷 Rename
- Product, `appId`, package name, window titles, repository and release pages.
- ⚠️ The old install is **not** removed automatically (`appId` changed), and an
  installed v2.3.1 gets **no** update notification. The release notes are the only
  announcement channel.
- `~/.unity_architect_ai` deliberately **unchanged** — it holds the key the API
  keys are encrypted with; renaming it would make them permanently unreadable.
  Same reasoning for the keyring service name and the markers written into the
  user's own files.
- "Unity" could not lead a product name under Unity's trademark guidelines; the
  new name is engine-neutral so Unreal/Godot support would not force a second
  rename.

### 🔌 The bundled Claude Code CLI was removed
- The SDK vendors its own copy and packaging swept it in — nobody chose it, and it
  **took priority over the user's own installation**, so their Claude Code was
  never used.
- Installer **561 → 308 MB** (−253 MB, 45%), measured from a rebuilt package.
- Consequence: the Claude path now needs Claude Code installed by the user.

### 🔒 Chat is gated on a usable provider
- New `GET /provider-ready`: cloud → has a key, CLI → installed (and signed in
  where that is measurable), Ollama → service reachable.
- The composer is disabled until then; previously the first reply could be a raw
  Python traceback.
- Fail-**open** when readiness cannot be measured: an unreachable backend does not
  mean a missing provider, and locking there would reproduce the very problem the
  gate exists to prevent.
- `loggedIn == null` means *not measured*, not *not logged in* — only a measured
  `false` blocks.

### 🌍 Internationalisation
- Starting language now derives from the OS. The old hard-coded `tr` also reached
  the model, so an English question came back in Turkish.
- Dictionary **157 → 373** keys; the whole renderer goes through it.
- Still Turkish: Electron main-process dialogs (37 strings, no dictionary access)
  and backend messages (~301, no mechanism).

### ⚖️ Licensing, privacy
- `LICENSE` + `THIRD-PARTY-NOTICES.md` are now **inside the installer** — they were
  never copied, while FFmpeg (which requires it) was shipped.
- FFmpeg licence stated **per platform** (Windows LGPL, macOS/Linux GPL) with the
  Windows source link added; it previously claimed GPL everywhere.
- Bundled .NET corrected: it is the **SDK** under Microsoft's terms, not MIT.
- Inherited MCP telemetry to a third-party endpoint **disabled**; it was on by
  default and undisclosed.

### 🐛 Fixes
- Codex error path leaked unredacted exception text to the UI (can carry the local
  MCP key); the Claude path already redacted, the sibling did not.
- Chat placeholder read "Ask zap a question…" (template leftover).
- Dead auth screens removed, which let the CSP drop its last remote image source.

### 🧪 Gates
backend **1111** · frontend **406** · tsc **0**


## v2.3.1 — 7 Ağustos 2026

Sohbete fotoğraf yapıştırınca oturumun düşmesi düzeltildi; ayrıca AI artık
Unity'de çalışan oyuna girdi gönderebiliyor.

### 🖼 Düzeltme — yapıştırılan görsel oturumu öldürüyordu
- Belirti boyuta bağlıydı, adede değil: iki fotoğrafla düşüyor, üçle düşmüyordu.
- Sebep: diske yazılan görseli model `Read` ile açınca sonuç base64 olarak tek bir
  satırda geri geliyor; o satırın 1 MB tavanı aşılınca hata kırpılmıyor, **oturum
  komple düşüyordu**. base64 ham boyutun ~4/3'ü olduğundan 750 KB'lık bir fotoğraf
  bile yetiyordu.
- İki katmanlı düzeltme: satır tavanı diğer CLI yoluyla hizalandı **ve** asıl sınır
  kaynağa kondu — büyük görseller diske yazılmadan önce küçültülüyor. Token
  maliyeti de düşüyor.
- Şeffaf PNG'lerde alfa kanalı korunuyor (ilk düzeltme denemesi onları siyah kareye
  çeviriyordu; denetim turu yakaladı).

### 🎮 Yeni — `manage_input`: AI oyunu oynayabiliyor
- Çalışan oyuna klavye, fare, gamepad ve UI girdisi gönderiliyor. Girdi Unity Input
  System'in sanal cihazlarına sürecin içinden basıldığı için **pencere odağı
  gerekmiyor**.
- Aksiyonlar: `describe`, `key`, `mouse_move`, `mouse_button`, `scroll`, `gamepad`,
  `ui_click`, `sequence`, `reset`.
- ⚠️ Yalnız yeni Input System'e göre yazılmış oyun kodu bu olayları görür; eski
  `Input.GetKey` kullanan projelerde çalışan tek yol `ui_click`'tir.

### 📸 Düzeltme — ekran görüntüsü eylem adı
- Modele var olmayan bir eylem adı öğretiliyordu; ekran görüntüsü istekleri bu
  yüzden boşa düşebiliyordu.

### 📖 Dokümantasyon
- README (TR + EN) 105 commit'lik gerçeklikle hizalandı: unityMCP onay davranışı
  artık sağlayıcı bazında doğru anlatılıyor, araç tablosu 46 aracın tamamını
  kapsıyor, eksik üç CLI sağlayıcısı (Cursor, OpenCode, Kimi Code) eklendi, model
  listeleri güncellendi ve gömülü .NET'in **SDK** olduğu (runtime değil) gerekçesiyle
  yazıldı.

---

## v2.3.0 — 2 Ağustos 2026

**Bu sürüm bir güvenlik sürümüdür.** v2.2.0'dan bu yana 125 commit geldi ve
100'ü düzeltme; ağırlığı, ürünün sırlarını, onay kapısını ve dosya erişimini
sertleştiren çalışma oluşturuyor. v2.2.0 kullanan herkesin güncellemesi
öneriliyor.

### 🔐 Güvenlik

**Yerel sır (backend ↔ Unity MCP paylaşımlı anahtarı)**
- Sır artık URL'de değil `X-API-Key` başlığında taşınıyor. Eskiden adres
  çubuğuna, log satırlarına ve süreç listelerine düşebiliyordu.
- Sır hiçbir CLI yapılandırma dosyasına yazılmıyor.
- Token dosyası Windows'ta ACL ile kilitli, yazımlar atomik; kimlik doğrulaması
  yola değil **açılmış dosya tanıtıcısına** bakıyor (sembolik bağ/junction ile
  yönlendirme kapandı).
- Sırrın hem yeni hem eski biçimi loglarda maskeleniyor.

**Prompt ve komut satırı**
- Kullanıcı prompt'u artık süreç argümanlarında taşınmıyor. Eskiden makinedeki
  herhangi bir süreç süreç listesinden prompt'un tamamını okuyabiliyordu.
- `[CMD]` kaydı prompt'un tamamını kalıcı bir dosyaya yazmıyor.

**Onay kapısı**
- Kapı Unity MCP boğazına kondu: **dokuz sağlayıcının dokuzu da** artık aynı
  kapıdan geçiyor. Öncesinde araç çağrıları kapıyı hiç görmüyordu.
- Kapı REST rotası üzerinden de atlatılamıyor.
- `delete_file` kapıya alındı.
- **Yazma onay kartı artık ne yazılacağını gösteriyor** — kart, yetkilendirilen
  girdinin tamamını taşıyor; öncesinde göremediğin bir şeyi onaylıyordun.
- Kart hangi projenin değişeceğini söylüyor; eşzamanlı adım turlarında
  otomatik onay "en az biri" değil "hepsi" kuralına bağlandı.
- Kullanıcının açtığı bir proje kapıyı devre dışı bırakamıyor.
- Codex onay hakemi `user` olarak sabitlendi ve yanıttan doğrulanıyor: onay
  isteği artık bir dil modeline devredilemiyor.

**Masaüstü uygulaması**
- **İçerik Güvenliği Politikası (CSP)** eklendi; Monaco editörü CDN yerine
  yerelden servis ediliyor — uygulama artık çalışmak için dış bir sunucuya
  bağlanmıyor.
- IPC çağrıları için kaynak kapısı: yalnız uygulamanın kendi sayfası çağırabilir.
- Workspace kökü yalnızca kullanıcının native diyalogla seçtiği bir yol olabilir.
- Uygulama uzak bir sayfaya gidemiyor, yeni pencere açamıyor, `<webview>`
  ekleyemiyor; dış linkler işletim sisteminin tarayıcısına gidiyor.

**Dosya erişimi**
- Sembolik bağ, junction, **sabit bağ** ve NTFS alternatif veri akışı yoluyla
  workspace dışına çıkma yolları kapatıldı.
- Okuma kapısında kontrol/kullanım yarışı kapatıldı: kapının onayladığı dosya
  ile okunan dosya artık aynı olmak zorunda, ve bu açılmış tanıtıcının
  kimliğiyle doğrulanıyor.
- Yapılandırma yazımları workspace dışındaki bir dosyanın üstüne yazamıyor.

**Diğer**
- Komut onay kapısındaki ön ek eşleşme atlatması kapatıldı.
- Ortam değişkeni sızıntısı sınıfı kapatıldı (ad eşleşmesi değil **değer**
  eşleşmesi).
- İndirilen tüm ikililer (OmniSharp, .NET SDK, uv, ffmpeg, yt-dlp) sabitlenmiş
  bir kütüğe ve özet doğrulamasına bağlandı.
- Kullanıcı projesine yazılan yapılandırma dosyaları `.gitignore`'a alındı.

### ✨ Yenilikler

- **Claude Opus 5** desteği.
- **Kimi K3 / Kimi CLI** sağlayıcısı.
- **Gemini 3.6** ve **Gemini 3.5 Lite**.
- OmniSharp için **.NET SDK pakete gömüldü** (macOS/Linux) — sıfır kurulum.
- Unity MCP: N+1 sorgu iyileştirmesi, kısmi eşleşme, toplu işlem zincirleme,
  kota/round-trip/no-op davranışları.

### 🐛 Düzeltmeler

- **Sohbetteki dosya linki artık dosyayı açıyor.** Yeşil dosya adına
  tıklayınca uygulama "resetleniyor" gibi görünüyordu — aslında pencere o
  adrese gidip arayüzü boşaltıyordu.
- **Beklenmedik veri artık tüm pencereyi boşaltmıyor:** uygulamaya bir hata
  sınırı eklendi, hata beyaz ekran yerine okunabilir bir panel gösteriyor.
- Model listesi bayat bir kapanış değeri yüzünden tüm API modellerini
  gizleyebiliyordu.
- Unity MCP, Editor'da domain reload sonrası yeniden bağlanmayı sürdürüyor.
- C# projesi (`csproj`) üretimi artık harici bir IDE kurulu olmasına bağlı değil.
- C# hover'ının asılması giderildi.
- `execute_code`'un atlatılabildiği yer düzeltildi.
- Windows'ta ürünün tamamını çalışmaz hale getirebilen üç hata giderildi.
- Uygulamanın kendi bağlantı afişi kendi onay kapısına takılıyordu.

### 📄 Lisans

- Proje **MIT + Commons Clause** ile lisanslandı; üçüncü parti bildirimleri eklendi.

### 🧪 Kalite

- Unity MCP sunucu test suite'i (1522 test) kalite kapısına bağlandı.
- Testler Windows'ta koşabilir hale getirildi; bu çalışma üç ürün hatası ortaya
  çıkardı.
- Dört bağımsız denetim turu koşuldu (biri tamamen dış gözle); üretilen
  bulguların tamamı kanıtlanabilir probe'larla üretildi ve kapatıldı.

### ⚠️ Bilinenler

- macOS derlemesi yalnız **Apple Silicon (arm64)**.
- Otomatik güncelleme **yalnız bildirim** yapar; indirme ve kurulum kullanıcıya
  bırakılır (uygulama imzasız olduğu için sessiz kurulum bilerek kapalı).
