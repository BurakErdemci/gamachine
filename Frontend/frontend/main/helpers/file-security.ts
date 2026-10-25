import fs from 'fs'
import path from 'path'

/**
 * Symlink-safe path resolver.
 * An existing path is canonicalised; a path that does not exist yet is resolved
 * down to its nearest existing ancestor, with the missing segments appended, so
 * that writing a NEW file under a symlinked directory is caught as well.
 *
 * ⚠️ `realpathSync.native`, not `realpathSync`. The JS implementation resolves
 * links but leaves an NTFS 8.3 short name as it found it — measured on this
 * repo's Windows: `<ws>\HARVES~1.GLB` came back as itself, while the native call
 * returned `<ws>\harvested-credentials.glbx`. Every caller here decides on the
 * resolved string and then opens it, so the two spellings have to collapse into
 * one before the decision. It cut both ways: the extension whitelist judged the
 * alias (`.GLB`) while `openSync` opened the real `.glbx`, and a workspace named
 * through its OWN short path was refused because it no longer matched itself.
 * The two calls throw the same errors, so the surrounding fallbacks are unchanged.
 */
function safeResolve(filePath: string): string {
  try {
    const absolutePath = path.resolve(filePath)
    if (fs.existsSync(absolutePath)) {
      return fs.realpathSync.native(absolutePath)
    }

    let currentPath = absolutePath
    const missingSegments: string[] = []

    while (!fs.existsSync(currentPath)) {
      const parentPath = path.dirname(currentPath)
      if (parentPath === currentPath) {
        return absolutePath
      }
      missingSegments.unshift(path.basename(currentPath))
      currentPath = parentPath
    }

    const resolvedExistingPath = fs.realpathSync.native(currentPath)
    return path.join(resolvedExistingPath, ...missingSegments)
  } catch {
    // realpath failed — fall through to the plain resolve below
  }
  return path.resolve(filePath)
}

/**
 * Uygulama içinden okunup yazılabilen metin dosyası uzantıları.
 * Editörde açma/kaydetme izni bu listeye bakar (dosya ağacı ise artık HER
 * dosyayı listeler; binary'ler görünür ama açılamaz). Unity'nin YAML tabanlı
 * asset formatları (prefab/anim/scene/mat…) metindir — editörde açılabilir.
 * Çalıştırılabilir/binary türler (fbx, png, dll…) bilerek dışarıda.
 */
export const TEXT_FILE_EXTENSIONS = [
  '.cs', '.shader', '.json', '.txt', '.md', '.xml', '.yaml', '.yml',
  '.compute', '.asmdef', '.asmref', '.cginc', '.hlsl', '.uss', '.uxml',
  // Unity YAML asset formatları
  '.anim', '.prefab', '.unity', '.mat', '.asset', '.controller',
  '.overridecontroller', '.physicmaterial', '.physicsmaterial2d', '.mixer',
  '.rendertexture', '.spriteatlas', '.terrainlayer', '.playable', '.signal',
  '.preset', '.guiskin', '.fontsettings', '.flare', '.giparams',
  '.shadervariants', '.mask', '.brush', '.meta',
  // Diğer metin türleri
  '.inputactions', '.csv', '.html', '.css', '.js', '.ini', '.cfg', '.rsp',
]

/**
 * Extension whitelist for the 3D model read channel.
 *
 * Whitelist, not blacklist: a model channel that could also hand back `.env` or
 * `.pem` is an exfiltration surface even when the file sits inside the
 * workspace, so the model gate names what it accepts instead of what it refuses.
 * Renderer keeps a mirror of this list (`MODEL_EXTENSIONS`); a test asserts the
 * two stay set-equal.
 */
export const MODEL_FILE_EXTENSIONS = [
  '.fbx', '.glb', '.gltf', '.dae', '.obj', '.stl', '.ply',
]

/**
 * The model channel's own size cap, independent of the 8 MiB text cap.
 *
 * The bytes cross IPC as an ArrayBuffer, which is *copied* into the renderer
 * heap; an unbounded read therefore inflates both Electron processes at once.
 */
export const MODEL_MAX_BYTES = 64 * 1024 * 1024

/**
 * Extension whitelist for the image read channel.
 *
 * Same reasoning as the model whitelist: a channel that can hand back arbitrary
 * bytes from inside the workspace is an exfiltration surface, so it names what
 * it accepts. Only formats a Chromium <img> can decode are here — the renderer
 * keeps a mirror (`IMAGE_EXTENSIONS`) and a test asserts the two stay set-equal.
 */
export const IMAGE_FILE_EXTENSIONS = [
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
]

/**
 * The image channel's own size cap, half the model cap.
 *
 * Like the model bytes, these are *copied* into the renderer heap on the way
 * through IPC, so the cap bounds both Electron processes at once. 32 MiB clears
 * any realistic Unity texture source (an 8192x8192 32-bit PNG compresses well
 * under it) while keeping the worst-case copy half of what a model costs.
 */
export const IMAGE_MAX_BYTES = 32 * 1024 * 1024

/**
 * The text channel's size cap.
 *
 * A large Unity scene or prefab YAML runs to 100 MiB and freezes Monaco, which
 * is what the ceiling is for. It lives here, exported, next to the model cap:
 * the same number was previously an inline literal in the `read-file` handler,
 * so the two caps could not be asserted or changed together.
 */
export const TEXT_MAX_BYTES = 8 * 1024 * 1024

/**
 * Read EXACTLY `size` bytes from an already-open descriptor, or null.
 *
 * Not `readFileSync(fd)`: that reads until EOF, so a file that grows between
 * `fstatSync` and the read hands back more bytes than the cap was checked
 * against. Bounding the loop by the size the cap was checked against closes
 * that window in one direction, and a file that SHRANK ends short of `size`,
 * which is reported as a failure rather than as a truncated read.
 *
 * Reading into an ArrayBuffer of our own also keeps Buffer's shared pool out of
 * the payload that crosses IPC.
 */
export function readExactly(fd: number, size: number): Uint8Array | null {
  const view = new Uint8Array(new ArrayBuffer(size))
  let read = 0
  while (read < size) {
    const n = fs.readSync(fd, view, read, size - read, read)
    if (n <= 0) break
    read += n
  }
  return read === size ? view : null
}

/**
 * Yazma/okuma izni sadece workspace içindeki Assets/Scripts/*.cs dosyalarına verilir.
 * Path traversal, symlink ve dışarı çıkış girişimlerini engeller.
 */
export function isAllowedUnityScriptPath(filePath: string, workspacePath: string): boolean {
  try {
    if (!filePath || !workspacePath) return false

    // Eğer yol absolute değilse, önce workspace ile birleştirip öyle kontrol etmeliyiz
    const absoluteFilePath = path.isAbsolute(filePath) ? filePath : path.resolve(workspacePath, filePath)

    const resolvedFile = safeResolve(absoluteFilePath)
    const resolvedWorkspace = safeResolve(workspacePath)
    const relativePath = path.relative(resolvedWorkspace, resolvedFile)

    // Workspace dışına çıkış kontrolü
    if (!relativePath || relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
      return false
    }

    const parts = relativePath.split(path.sep).filter(Boolean)
    if (parts.length < 3 || parts[0] !== 'Assets' || parts[1] !== 'Scripts') {
      return false
    }

    // Sadece Assets/Scripts altındaki .cs dosyalarına yazma izni ver
    return path.extname(resolvedFile).toLowerCase() === '.cs'
  } catch {
    return false
  }
}

/**
 * Workspace dosya ağacını gezerken yalnızca seçili workspace içindeki yol okunabilir.
 * Symlink çözümü dahildir.
 */
export function isAllowedWorkspacePath(targetPath: string, workspacePath: string): boolean {
  try {
    if (!targetPath || !workspacePath) return false

    const resolvedTarget = safeResolve(targetPath)
    const resolvedWorkspace = safeResolve(workspacePath)
    const relativePath = path.relative(resolvedWorkspace, resolvedTarget)

    if (!relativePath) {
      return true
    }

    return !relativePath.startsWith('..') && !path.isAbsolute(relativePath)
  } catch {
    return false
  }
}

/** Çözülmüş iki yol arasında kapsama kontrolü — ikisi de ÇÖZÜLMÜŞ olmalı. */
function icerideMi(cozulmusHedef: string, cozulmusWorkspace: string): boolean {
  const rel = path.relative(cozulmusWorkspace, cozulmusHedef)
  if (!rel) return true
  return !rel.startsWith('..') && !path.isAbsolute(rel)
}

/**
 * Yol bir NTFS alternatif veri akışını mı adlandırıyor?
 *
 * Windows `host.exe:notes.txt` yazımını `host.exe`'nin `notes.txt` akışı olarak
 * okuyor, ama `path.extname` sondaki `.txt`'yi dosyanın uzantısı sanıyor.
 * Sonuç: uzantı beyaz listesi, aslında `.exe` olan bir dosyanın baytlarını
 * yetkilendiriyordu — ve o akış dizin listesinde hiç görünmüyor (dış denetim
 * bulgusu, probe ile üretildi). Sürücü harfindeki iki nokta (`C:\`) meşru, o
 * yüzden yalnız ondan SONRAKİ kısma bakılıyor.
 *
 * ⚠️ YALNIZ Windows (doğrulama turu bulgusu
 * `ads-check-rejects-legitimate-posix-paths`): POSIX'te iki nokta sıradan bir
 * dosya adı karakteri, üstelik macOS Finder'da `/` içeren bir klasör adı disk
 * üzerinde `:` olarak saklanıyor. Platform koşulu olmadan bu kontrol orada
 * meşru dosyaları sessizce açılamaz yapıyordu.
 *
 * `platform` parametre: `process.platform` test koşumunda değiştirilemiyor, ve
 * platforma bağlı bir dalı sınayamamak bu depoda ölçülmüş bir arıza sınıfı.
 */
export function alternatifVeriAkisiMi(
  cozulmusYol: string,
  platform: string = process.platform,
): boolean {
  if (platform !== 'win32') return false
  const surucusuz = cozulmusYol.length > 2 && cozulmusYol[1] === ':'
    ? cozulmusYol.slice(2)
    : cozulmusYol
  return surucusuz.includes(':')
}

/**
 * Which kind of read the gate is deciding on.
 * The gates are identical; only the extension whitelist differs.
 */
export type OkumaTuru = 'metin' | 'model' | 'image'

/**
 * Why the gate refused. `unsupported` is the extension whitelist alone; the
 * path-level gates (containment, ADS, hardlink) get their own code so a caller
 * can tell "wrong file type" apart from "this file is out of bounds".
 */
export type RedSebebi = 'unsupported' | 'denied'

/** Okuma kapısının kararı ve o kararın DAYANDIĞI çözülmüş yol. */
export type OkumaKarari = {
  izinli: boolean
  cozulmusYol: string
  sebep?: RedSebebi
}

/**
 * Okuma kapısının tek karar noktası: yolu BİR KEZ çözer, kararı o çözülmüş yol
 * üzerinde verir ve açılması gereken yolu da geri döndürür.
 *
 * ⚠️ Doğrulama turu bulgusu (`check-open-resolve-divergence`): bir önceki
 * düzeltme "ham yol yerine çözülmüş yolu aç" diyordu, ama kapı kendi içinde bir
 * kez, çağıran ikinci kez çözüyordu. İki çözüm arasında bir ara bileşen
 * junction'a çevrilince kontrol edilen dosya ile açılan dosya yine ayrışıyordu —
 * yani düzeltme, kapattığını SANDIĞI yarışı kapatmamıştı. Karar ile açış artık
 * aynı dizgeye bakıyor.
 *
 * ⚠️ Tek çözüm yarışı DARALTIR, kapatmaz: bu yolun bir bileşeni açıştan hemen
 * önce de değiştirilebilir. Sınıfı kapatan kontrol çağırandaki AÇILMIŞ TANITICI
 * doğrulaması (`background.ts`) — o yola değil inode'a bakıyor, dolayısıyla
 * açıştan sonra yapılan bir takas kararı geriye dönük bozamıyor.
 */
export function okumaKarariVer(
  filePath: string,
  workspacePath: string,
  tur: OkumaTuru = 'metin',
): OkumaKarari {
  const reddet = (yol: string, sebep: RedSebebi = 'denied'): OkumaKarari =>
    ({ izinli: false, cozulmusYol: yol, sebep })
  try {
    if (!filePath || !workspacePath) return reddet('')

    const cozulmus = safeResolve(filePath)
    const cozulmusWs = safeResolve(workspacePath)

    if (!icerideMi(cozulmus, cozulmusWs)) return reddet(cozulmus)

    // Gerekçesi `alternatifVeriAkisiMi`'nin başında.
    if (alternatifVeriAkisiMi(cozulmus)) return reddet(cozulmus)

    // ⚠️ SABİT BAĞ (dış denetim bulgusu, probe ile üretildi). `realpathSync`
    // sembolik bağı ve junction'ı çözüyor, ama sabit bağ bir bağ DEĞİL — ikinci
    // bir addır. Gerçek yolu workspace içinde kalır, baytları dışarıdaki
    // dosyanındır, dolayısıyla kapsama kontrolü onu kabul ediyordu.
    //
    // ⭐ Aynı sınıf backend'de ZATEN kapalıydı (`safe_paths._dogrula_kimlik`,
    // `st_nlink > 1`). Yani bu, güvenlik kararının iki kopyasının ayrışmasıydı —
    // bu depoda adı konmuş bir arıza sınıfı. Karar burada tekrar ediliyor çünkü
    // iki süreç ayrı: Python backend'in kontrolü Electron ana sürecini kapsamaz.
    //
    // Dizinler muaf: NTFS'te dizinlerin bağlantı sayısı alt dizin sayısıyla
    // birlikte artıyor, yani onlarda `> 1` normaldir.
    //
    // ⚠️ EN İYİ ÇABA, varlık ŞARTI DEĞİL. Bu fonksiyon saf bir yol politikası
    // olarak da çağrılıyor (var olmayan yollarla; testleri buna dayanıyor),
    // dolayısıyla "dosya yok" burada bir RED sebebi olamaz. Sınıfı kapatan
    // KESİN kontrol zaten `read-file` handler'ında, AÇILMIŞ TANITICI üzerinde
    // koşuyor — yola değil inode'a bakıyor, yani kontrol/kullanım yarışına da
    // kapalı. Buradaki erken eleme onu tamamlıyor, yerine geçmiyor.
    try {
      const st = fs.lstatSync(cozulmus)
      if (st.isFile() && st.nlink > 1) {
        return reddet(cozulmus)
      }
    } catch {
      /* yol henüz yok ya da okunamıyor — kararı fd üzerindeki kontrol verecek */
    }

    // Only the whitelist differs between the two read kinds; every gate above
    // this line is shared, so the two decisions cannot drift apart.
    const beyazListe = tur === 'model'
      ? MODEL_FILE_EXTENSIONS
      : tur === 'image'
        ? IMAGE_FILE_EXTENSIONS
        : TEXT_FILE_EXTENSIONS
    if (!beyazListe.includes(path.extname(cozulmus).toLowerCase())) {
      return reddet(cozulmus, 'unsupported')
    }

    return { izinli: true, cozulmusYol: cozulmus }
  } catch {
    return reddet('')
  }
}

/**
 * AÇILMIŞ bir tanıtıcının hâlâ workspace içindeki bir dosyayı gösterdiğini
 * doğrular. Okuma kapısında sınıfı asıl kapatan kontrol budur.
 *
 * Yolu bir kez çözmek yarışı DARALTIR ama kapatmaz — açılacak yolun bir bileşeni
 * `openSync` çağrısından hemen önce de takas edilebilir. Bu yüzden açıştan
 * SONRA iki şey birlikte soruluyor:
 *   (1) yol şu an hâlâ workspace içinde bir dosyayı adlandırıyor mu,
 *   (2) elimizdeki tanıtıcı gerçekten O dosya mı (aygıt + inode).
 * Yalnız (1) olsaydı saldırgan açıştan sonra geri takas edip kontrolü
 * kandırırdı; yalnız (2) olsaydı dışarıdaki bir dosya kendisiyle eşleşip
 * geçerdi. İkisi birlikte açılmış tanıtıcıyı kapsama kararına bağlıyor — ve bir
 * tanıtıcının hangi dosyayı gösterdiği, açıldıktan sonra artık değiştirilemez.
 *
 * ⚠️ Bu mantık bilerek `background.ts` içinde SATIR İÇİ bırakılmadı: ana süreç
 * test koşumunda yüklenmiyor, dolayısıyla orada kalan her kontrol sınanamaz
 * oluyor. Aynı ders bu depoda aynı denetim turunda iki kez ödendi.
 *
 * ⚠️ EXACT 64-bit ids, not doubles. A plain `fs.Stats.ino` is a JS number, and a
 * double carries only 53 bits of integer precision; an NTFS file reference
 * number is 64 bits (48-bit MFT index + 16-bit sequence). Measured on this
 * volume: 201 of 400 freshly created files had an `ino` above 2^53 (~1.1e17,
 * where doubles are spaced 16 apart), so two DIFFERENT files whose ids differ by
 * less than that spacing compare equal — a false accept on a security gate. No
 * collision was observed firing (0 in 700 tries), so this is hardening, not a
 * live bug. `{ bigint: true }` is the only way Node hands back the exact value,
 * hence the `BigIntStats` parameter type: a caller with a plain `Stats` cannot
 * supply an exact id, so the type refuses it rather than silently rounding.
 *
 * @param acilanStat açılmış tanıtıcının `fstatSync(fd, { bigint: true })`
 *   sonucu — yolun değil.
 */
export function taniticiKapsamdaMi(
  acilanStat: fs.BigIntStats,
  acilanYol: string,
  workspacePath: string,
): boolean {
  try {
    // Ayrıca `realpathSync` ÇAĞRILMIYOR: aşağıdaki ikisi de yolu kendi içinde
    // zaten çözüyor (`isAllowedWorkspacePath` → `safeResolve`, `statSync` →
    // bağları takip eder). Fazladan bir çözüm adımı mutasyonla ölçüldüğünde
    // hiçbir mutantı yakalamıyordu — yani davranışa katkısı yok, yalnız
    // "burada bir şey yapılıyor" izlenimi veriyordu.
    if (!isAllowedWorkspacePath(acilanYol, workspacePath)) return false
    const kimlik = fs.statSync(acilanYol, { bigint: true })
    return kimlik.ino === acilanStat.ino && kimlik.dev === acilanStat.dev
  } catch {
    return false
  }
}

/**
 * An OPERATIONAL binary-read failure: generic to the renderer, named in the log.
 *
 * The renderer keeps seeing `denied` on purpose — the caller must not learn from
 * an error code whether a path exists, is a directory, or is merely unreadable.
 * But collapsing every cause into that one value left the main process with no
 * trace at all, so a disk, permission or descriptor failure was indistinguishable
 * from a policy refusal. `console.error` is where this process already logs a
 * rejected IPC call, and it is mirrored to the on-disk log a packaged build has
 * instead of a console.
 *
 * Policy refusals above this point are decisions, not failures, and stay silent.
 */
function denyWithCause(etiket: string, fullPath: string, cause: unknown): BinaryReadResult {
  const reason = cause instanceof Error
    ? `${(cause as NodeJS.ErrnoException).code ?? cause.name}: ${cause.message}`
    : String(cause)
  console.error(`[${etiket}] '${fullPath}' could not be read — ${reason}`)
  return { error: 'denied' }
}

export type BinaryReadResult =
  | { path: string; name: string; data: ArrayBuffer }
  | { error: 'unsupported' | 'too-large' | 'denied' | 'busy' }

/** Kept as the model channel's own name; the shape is shared with images. */
export type ModelReadResult = BinaryReadResult

/** Per-channel numbers, so the shared read below stays one function. */
const IKILI_KANALLAR = {
  model: { tavan: MODEL_MAX_BYTES, etiket: 'model-read' },
  image: { tavan: IMAGE_MAX_BYTES, etiket: 'image-read' },
} as const

/**
 * The binary read, gate chain included, from one open file descriptor.
 *
 * ⚠️ ONE function for every binary channel, parameterised by kind. Two copies of
 * a read gate is this repo's named defect class — the earlier model-only copy
 * would have had to be duplicated verbatim for images, and the copies drift
 * silently because nothing fails when only one of them is hardened.
 *
 * ⚠️ Deliberately NOT inlined in `background.ts`: the main process is not loaded
 * under the test runner, so anything left there cannot be exercised — a lesson
 * this repo has already paid for twice (see `taniticiKapsamdaMi`). The handler
 * keeps the sender-trust and workspace-trust guards and delegates the rest here.
 *
 * The fd is opened ONCE and every later question (size, hardlink, containment,
 * the read itself) is asked of that descriptor. Reopening by path after a check
 * would reintroduce the measured check/use race.
 */
export function readBinaryFileFromWorkspace(
  fullPath: string,
  workspacePath: string,
  tur: 'model' | 'image',
): BinaryReadResult {
  const { tavan, etiket } = IKILI_KANALLAR[tur]
  const karar = okumaKarariVer(fullPath, workspacePath, tur)
  if (!karar.izinli) return { error: karar.sebep ?? 'denied' }

  let fd: number | null = null
  try {
    fd = fs.openSync(karar.cozulmusYol, 'r')
    // `{ bigint: true }` so the identity check below compares exact 64-bit ids;
    // the reason is in `taniticiKapsamdaMi`.
    const st = fs.fstatSync(fd, { bigint: true })
    if (!st.isFile()) return { error: 'denied' }
    // The cap is enforced BEFORE any Number() conversion: a size above 2^53
    // would round on the way through a double, so converting first could turn
    // an over-cap file into an under-cap one.
    if (st.size > BigInt(tavan)) return { error: 'too-large' }
    // Rechecked on the fd: catches a second name created between the gate and
    // the open.
    // `BigInt(1)`, not `1n`: tsconfig targets ES2017 (see background.ts).
    if (st.nlink > BigInt(1)) return { error: 'denied' }
    if (!taniticiKapsamdaMi(st, karar.cozulmusYol, workspacePath)) {
      return { error: 'denied' }
    }

    const view = readExactly(fd, Number(st.size))
    if (!view) {
      return denyWithCause(etiket, fullPath, `short read: fewer than ${st.size} bytes available`)
    }
    // `fullPath` is returned rather than the resolved path, for the same reason
    // the text handler does it: the file tree addresses files by this name.
    return { path: fullPath, name: path.basename(fullPath), data: view.buffer as ArrayBuffer }
  } catch (err) {
    return denyWithCause(etiket, fullPath, err)
  } finally {
    if (fd !== null) { try { fs.closeSync(fd) } catch { /* close failure does not invalidate the read */ } }
  }
}

/**
 * Dosya yazma izni: workspace içi + metin beyaz listesi.
 * - .cs için eski kural KORUNUR (yalnızca Assets/Scripts altı — AI kod üretimi oraya akar).
 * - Diğer metin türleri (md, txt, json, shader…) workspace içinde her yere yazılabilir
 *   (örn. kök dizinde CLAUDE.md oluşturma). Path traversal/symlink koruması aynen.
 */
export function isAllowedWorkspaceWriteFile(filePath: string, workspacePath: string): boolean {
  try {
    if (!filePath || !workspacePath) return false

    const absoluteFilePath = path.isAbsolute(filePath) ? filePath : path.resolve(workspacePath, filePath)
    const ext = path.extname(safeResolve(absoluteFilePath)).toLowerCase()

    if (ext === '.cs') {
      return isAllowedUnityScriptPath(filePath, workspacePath)
    }
    if (!TEXT_FILE_EXTENSIONS.includes(ext)) {
      return false
    }
    return isAllowedWorkspacePath(absoluteFilePath, workspacePath)
  } catch {
    return false
  }
}

/**
 * How many model reads the main process will have in flight at once.
 *
 * Two, because the per-file cap was the channel's ONLY bound and the cost of
 * ignoring the count is linear. Measured on the real handler with files sitting
 * exactly on the 64 MiB cap — each call individually legal:
 *
 *   in flight | wall   | main process answered nothing for | payload alive
 *   1         |  18 ms |   9 ms                            |  64 MiB
 *   2         |  34 ms |  24 ms                            | 128 MiB
 *   4         |  62 ms |  52 ms                            | 256 MiB
 *   8         | 129 ms | 120 ms                            | 512 MiB
 *
 * Linear, no knee: 8 calls cost 8x, and nothing made 8 the ceiling — 32 would
 * be 2 GiB resident and roughly half a second of frozen main process, since the
 * read is synchronous and shares the one thread with every other IPC call, menu
 * action and window event.
 *
 * Two rather than one: the app's only caller is the preview panel, which loads
 * one model at a time, and a second slot lets a newly selected model start
 * while the previous request is still unwinding. Two rather than four: at two
 * the worst case is 128 MiB and a 24 ms stall, under the frame budget where the
 * window visibly stops responding; four already costs 52 ms.
 */
export const MODEL_READ_MAX_IN_FLIGHT = 2

let binaryReadsInFlight = 0

/**
 * Admission control in front of every binary read.
 *
 * Over the limit the call is REFUSED rather than queued: queuing would bound
 * the stall but not the memory, because every queued caller still ends up
 * holding a payload, and the measured harm is the simultaneous payload.
 *
 * ONE counter for models and images together, not one each. The bound exists to
 * cap the bytes resident in the main process at a moment in time, and that total
 * does not care which channel carried them — two independent limits of two would
 * allow four payloads at once, which is the state the measurement above rejected.
 *
 * The `await` is load-bearing, not decoration. `readBinaryFileFromWorkspace` is
 * synchronous end to end, so without a yield each invocation would run to
 * completion before the next one was even entered and the counter could never
 * read higher than one — a limit that never limits. Yielding first makes
 * overlapping invocations actually overlap, which is what the count then bounds.
 */
async function readBinaryGuarded(
  fullPath: string,
  workspacePath: string,
  tur: 'model' | 'image',
): Promise<BinaryReadResult> {
  if (binaryReadsInFlight >= MODEL_READ_MAX_IN_FLIGHT) return { error: 'busy' }
  binaryReadsInFlight++
  try {
    await Promise.resolve()
    return readBinaryFileFromWorkspace(fullPath, workspacePath, tur)
  } finally {
    binaryReadsInFlight--
  }
}

export function readModelFileGuarded(
  fullPath: string,
  workspacePath: string,
): Promise<ModelReadResult> {
  return readBinaryGuarded(fullPath, workspacePath, 'model')
}

export function readImageFileGuarded(
  fullPath: string,
  workspacePath: string,
): Promise<BinaryReadResult> {
  return readBinaryGuarded(fullPath, workspacePath, 'image')
}
