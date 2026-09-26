import { describe, it, expect } from 'vitest'
import {
  ALLOWED_INVOKE_CHANNELS,
  assertAllowedInvokeChannel,
} from '../main/helpers/ipc-whitelist'

describe('IPC Whitelist — izinli kanallar', () => {
  const fileChannels = [
    'get-backend-base-url',
    'open-file-dialog',
    'open-folder-dialog',
    'read-directory',
    'read-file',
    'write-file',
    'file-exists',
    'write-multiple-files',
  ]

  for (const ch of fileChannels) {
    it(`dosya kanalı geçer: ${ch}`, () => {
      expect(() => assertAllowedInvokeChannel(ch)).not.toThrow()
    })
  }

  it('app-token-get kanalı geçer', () => {
    expect(() => assertAllowedInvokeChannel('app-token-get')).not.toThrow()
  })
})

describe('IPC Whitelist — izinsiz kanallar engellenir', () => {
  const blockedChannels = [
    'exec',
    'shell',
    'eval',
    '../../../etc/passwd',
    'read-file; rm -rf /',
    'session-get\0malicious',
    '',
    'OPEN-FILE-DIALOG',          // büyük harf farkı
    'open_file_dialog',          // alt çizgi
    'write-file-extra',          // prefix match değil, tam eşleşme
    'session',
    'session-get',
    'session-set',
    'session-clear',
    'get',
    'node:fs',
  ]

  for (const ch of blockedChannels) {
    it(`engellenir: "${ch}"`, () => {
      expect(() => assertAllowedInvokeChannel(ch)).toThrow('IPC channel izinsiz')
    })
  }
})

describe('IPC Whitelist — Set doğruluğu', () => {
  // Sayı bilerek sabit: yeni bir IPC kanalı yüzeyi genişletiyor ve bu test
  // patlamadan kimse fark etmiyor. 24 → 26, 31 Ağu 2026: Docker modunda
  // çalışma alanı yolunun iki yönde çevrilmesi için eklendi
  // ('backend-workspace-path', 'host-workspace-path'). İkisi de saf eşleme,
  // dosya sistemine dokunmuyor ve ana süreçten dışarı veri sızdırmıyor.
  //
  // 26 → 27, 31 Ağu 2026: 3D model önizleme için 'read-model-file' eklendi.
  // Bu kanal dosya sistemine DOKUNUYOR, o yüzden metin okuma kapısının tüm
  // kapılarını (kapsama, ADS, sabit bağ, fd/inode doğrulaması) paylaşıyor ve
  // üstüne kendi uzantı beyaz listesi (MODEL_FILE_EXTENSIONS) ile kendi boyut
  // tavanı (MODEL_MAX_BYTES) var — yani yüzey model dosyalarıyla sınırlı.
  //
  // 27 → 28, 2 Eyl 2026: görsel önizleme için 'read-image-file' eklendi. O da
  // dosya sistemine dokunuyor ama YENİ bir kapı açmıyor: model kanalının kapı
  // zincirini, uçuştaki okuma bütçesini ve karar fonksiyonunu aynen paylaşıyor;
  // yalnız beyaz listesi (IMAGE_FILE_EXTENSIONS) ve tavanı (IMAGE_MAX_BYTES)
  // kendine ait, yani yüzey tarayıcının çözebildiği görsel biçimleriyle sınırlı.
  //
  // 28 → 29, 25 Eyl 2026: 'approval-mode-set' eklendi. Küresel onay modunu
  // (auto/step) yalnız uygulama arayüzü değiştirebilsin diye yazma ana süreçten
  // geçiyor; backend'e giden UI sırrı renderer'a hiç verilmiyor.
  //
  // 29 -> 30, 26 Sep 2026: 'notify' (desktop notifications for background
  // chats). It touches no file and returns no data: the main process shows a
  // title and a body after checking the payload's shape, and on click sends
  // back only the conversation id it was given (see helpers/notify.ts).
  it('tam olarak 30 kanal içerir', () => {
    expect(ALLOWED_INVOKE_CHANNELS.size).toBe(30)
  })

  it("notify kanalı whitelist'te", () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('notify')).toBe(true)
  })

  it("approval-mode-set kanalı whitelist'te", () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('approval-mode-set')).toBe(true)
  })

  it("read-image-file kanalı whitelist'te", () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('read-image-file')).toBe(true)
  })

  it('read-model-file kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('read-model-file')).toBe(true)
  })

  it('çalışma alanı yolu çeviri kanalları whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('backend-workspace-path')).toBe(true)
    expect(ALLOWED_INVOKE_CHANNELS.has('host-workspace-path')).toBe(true)
  })

  it('git-status kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('git-status')).toBe(true)
  })

  it('her kanal benzersiz', () => {
    const arr = [...ALLOWED_INVOKE_CHANNELS]
    expect(arr.length).toBe(new Set(arr).size)
  })

  it('dosya kanallarının hepsi whitelist\'te', () => {
    const expected = ['get-backend-base-url', 'open-file-dialog', 'open-folder-dialog', 'read-directory',
      'read-file', 'write-file', 'file-exists', 'write-multiple-files',
      'create-file', 'create-folder', 'rename-entry', 'delete-entry', 'move-entry',
      'save-file-dialog', 'export-text-file', 'import-text-file', 'delete-file']
    for (const ch of expected) {
      expect(ALLOWED_INVOKE_CHANNELS.has(ch)).toBe(true)
    }
  })

  it('app-token-get kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('app-token-get')).toBe(true)
  })

  it('path-exists kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('path-exists')).toBe(true)
  })

  it('open-video-dialog kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('open-video-dialog')).toBe(true)
  })

  it('terminal kanalları whitelist\'te', () => {
    for (const ch of ['terminal-spawn', 'terminal-write', 'terminal-resize']) {
      expect(ALLOWED_INVOKE_CHANNELS.has(ch)).toBe(true)
    }
  })
})
