import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, it, expect } from 'vitest'
import {
  isAllowedUnityScriptPath,
  isAllowedWorkspacePath,
  okumaKarariVer,
} from '../main/helpers/file-security'

const WS = '/project/MyGame'
let tempRoot = ''

beforeEach(() => {
  tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'unity-file-security-'))
})

afterEach(() => {
  if (tempRoot) {
    fs.rmSync(tempRoot, { recursive: true, force: true })
    tempRoot = ''
  }
})

describe('isAllowedUnityScriptPath — geçerli yollar', () => {
  it('Assets/Scripts doğrudan altındaki .cs dosyasına izin verir', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Player.cs`, WS)).toBe(true)
  })

  it('Assets/Scripts altındaki alt klasördeki .cs dosyasına izin verir', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Enemy/AI.cs`, WS)).toBe(true)
  })

  it('derin iç içe geçmiş yola izin verir', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/UI/Menus/PauseMenu.cs`, WS)).toBe(true)
  })
})

describe('isAllowedUnityScriptPath — uzantı kısıtlaması', () => {
  it('.js dosyasını reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Player.js`, WS)).toBe(false)
  })

  it('.txt dosyasını reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/notes.txt`, WS)).toBe(false)
  })

  it('.cs uzantısı olmayan dosyayı reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Player`, WS)).toBe(false)
  })

  it('.CS (büyük harf) uzantısını kabul eder (case-insensitive)', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Player.CS`, WS)).toBe(true)
  })
})

describe('isAllowedUnityScriptPath — path traversal koruması', () => {
  it('../ ile workspace dışına çıkmayı reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/../../etc/passwd`, WS)).toBe(false)
  })

  it('../../ çoklu traversal reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/../../../etc/shadow.cs`, WS)).toBe(false)
  })

  it('symlink ile workspace dışına çıkma girişimini reddeder (absolute path)', () => {
    expect(isAllowedUnityScriptPath('/etc/passwd.cs', WS)).toBe(false)
  })

  it('başka bir workspace içindeki geçerli yolu reddeder', () => {
    expect(isAllowedUnityScriptPath('/other-project/Assets/Scripts/Hack.cs', WS)).toBe(false)
  })

  it('symlink klasör altına yeni dosya yazmayı reddeder', () => {
    const workspace = path.join(tempRoot, 'workspace')
    const scriptsDir = path.join(workspace, 'Assets', 'Scripts')
    const outsideDir = path.join(tempRoot, 'outside')
    const linkDir = path.join(scriptsDir, 'Linked')

    fs.mkdirSync(scriptsDir, { recursive: true })
    fs.mkdirSync(outsideDir, { recursive: true })
    try {
      fs.symlinkSync(outsideDir, linkDir, 'dir')
    } catch (err) {
      // ATLAMA YOK (denetim bulgusu `silently-skipped-security-test`). Burası
      // bir güvenlik iddiası: workspace'ten DIŞARI çıkan bir bağın altına yazma
      // reddedilmeli. Eskiden EPERM gelince `ctx.skip()` çağrılıyordu, yani
      // Developer Mode kapalı her Windows makinesinde — bu kaçışı kurmanın en
      // kolay olduğu yerde — iddia hiç koşmadan yeşil rapor ediliyordu.
      //
      // Windows'ta 'dir' türü symlink yönetici/Developer Mode ister ama JUNCTION
      // istemez, ve junction da tam olarak aynı şekilde dışarı çıkar. POSIX'te
      // 'junction' yok sayılıp sıradan symlink kurulur, yani ikinci deneme her
      // yerde geçerli. İkisi de kurulamıyorsa test KIRILIR: ölçemediğimiz bir
      // güvenlik özelliğini geçmiş saymak, hiç ölçmemekten kötü.
      if ((err as NodeJS.ErrnoException)?.code !== 'EPERM') throw err
      fs.symlinkSync(outsideDir, linkDir, 'junction')
    }

    const targetFile = path.join(linkDir, 'Exploit.cs')
    expect(isAllowedUnityScriptPath(targetFile, workspace)).toBe(false)
  })
})

describe('isAllowedUnityScriptPath — klasör yapısı kısıtlaması', () => {
  it('Assets/ olmadan Scripts/ altındaki dosyayı reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Scripts/Player.cs`, WS)).toBe(false)
  })

  it('Assets/ altında Scripts/ olmayan dosyayı reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Player.cs`, WS)).toBe(false)
  })

  it('workspace kökündeki .cs dosyasını reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Player.cs`, WS)).toBe(false)
  })

  it('Assets/Resources altındaki .cs dosyasını reddeder', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Resources/config.cs`, WS)).toBe(false)
  })

  it('Assets/Scripts dizinin kendisini reddeder (klasör, dosya değil)', () => {
    // path depth 2 → parts.length < 3
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts`, WS)).toBe(false)
  })
})

describe('isAllowedUnityScriptPath — hata durumları', () => {
  it('boş filePath ile false döner', () => {
    expect(isAllowedUnityScriptPath('', WS)).toBe(false)
  })

  it('boş workspacePath ile false döner', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Scripts/Player.cs`, '')).toBe(false)
  })
})

describe('isAllowedWorkspacePath — workspace okuma sınırı', () => {
  it('workspace kökünü kabul eder', () => {
    expect(isAllowedWorkspacePath(WS, WS)).toBe(true)
  })

  it('workspace içindeki klasörü kabul eder', () => {
    expect(isAllowedWorkspacePath(`${WS}/Assets/Scripts/UI`, WS)).toBe(true)
  })

  it('workspace dışındaki klasörü reddeder', () => {
    expect(isAllowedWorkspacePath('/tmp', WS)).toBe(false)
  })

  it('traversal ile dışarı çıkışı reddeder', () => {
    expect(isAllowedWorkspacePath(`${WS}/Assets/../..`, WS)).toBe(false)
  })
})

describe('okumaKarariVer — dosya okuma sınırı', () => {
  it('workspace içindeki .cs dosyasını kabul eder', () => {
    expect(okumaKarariVer(`${WS}/Assets/Scripts/Player.cs`, WS).izinli).toBe(true)
  })

  it('workspace içindeki .txt/.md gibi metin dosyalarını kabul eder', () => {
    // Yeni davranış: TEXT_FILE_EXTENSIONS listesindeki tüm türler okunabilir
    // (dosya ağacı ne listeliyorsa editörde de açılabilsin).
    expect(okumaKarariVer(`${WS}/Assets/Scripts/notes.txt`, WS).izinli).toBe(true)
    expect(okumaKarariVer(`${WS}/README.md`, WS).izinli).toBe(true)
  })

  it('binary/çalıştırılabilir türleri (.exe/.dll) reddeder', () => {
    expect(okumaKarariVer(`${WS}/Assets/tool.exe`, WS).izinli).toBe(false)
    expect(okumaKarariVer(`${WS}/Assets/lib.dll`, WS).izinli).toBe(false)
  })

  it('workspace dışındaki .cs dosyasını reddeder', () => {
    expect(okumaKarariVer('/tmp/Hack.cs', WS).izinli).toBe(false)
  })
})
