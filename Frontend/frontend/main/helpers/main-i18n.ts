import { app } from 'electron'

export type UiLang = 'tr' | 'en'

let announced: UiLang | undefined

export function setUiLanguage(l: unknown): void {
  if (l === 'tr' || l === 'en') announced = l
}

export function uiLanguage(): UiLang {
  if (announced) return announced
  try {
    const locale = app?.getLocale()
    if (typeof locale === 'string') return locale.toLowerCase().startsWith('tr') ? 'tr' : 'en'
  } catch { /* Electron may be unavailable in unit tests or before app readiness. */ }
  return 'tr'
}

export const TR = {
  notePlaceholder: 'Yorum ekle…',
  noteLabel: 'Yorum ekle',
  noteLine: 'satır',
  noteCancel: 'vazgeç',
  noteSave: 'Kaydet',
  noteLimit: 'En fazla {max} not',
  noteSaveFailed: 'Not kaydedilemedi, tekrar dene',
  modeInvalid: 'Geçersiz çalışma modu.',
  modeSendFailed: 'Çalışma modu backend’e iletilemedi.',
  annotateBar: 'İşaretleme · bir öğeye tıkla ya da alan çiz · Esc çıkış',
  updateRelease: 'Sürüm sayfasını aç',
  close: 'Kapat',
  updateFailedTitle: 'Güncelleme doğrulanamadı',
  updateFailedMessage: 'Yeni sürümün imzası doğrulanamadı, bu yüzden indirilip kurulmadı.',
  updateFailedDetail: 'Yeni sürümü sürüm sayfasından elle indirebilirsin.',
  updateRestart: 'Şimdi yeniden başlat',
  updateLater: 'Sonra',
  updateReadyTitle: 'Güncelleme hazır',
  updateReadyMessage: 'Yeni sürüm hazır: v{version}',
  updateReadyDetail: 'Şimdi yeniden başlatırsan güncelleme kurulur. "Sonra" dersen uygulamayı kapattığında kurulur.',
  authorizeLegacy: 'Uygulama bu klasörün içindeki dosyaları okuyup yazabilecek. Bu soruyu bir kez görüyorsun: daha önce seçtiğin çalışma klasörü yeni güvenlik kaydında yok. Emin değilsen İptal de ve klasörü uygulama içinden yeniden seç.',
  authorize: 'Yetkilendir',
  cancel: 'İptal',
  authorizeTitle: 'Çalışma klasörünü yetkilendir',
  authorizeMessage: 'Bu klasöre erişim izni verilsin mi?',
  authorizeDetail: 'Uygulama bu klasörün içindeki dosyaları okuyup yazabilecek.',
  recycleBin: 'Geri Dönüşüm Kutusu',
  trash: 'Çöp Kutusu',
  trashMessage: '"{name}" projesinin klasörü taşınacak',
  trashDetail: "{path}\n\n{bin}'na taşınacak. İstersen {bin} içinden geri yükleyebilirsin.",
  trashGo: "{bin}'na taşı",
  trashCancel: 'Vazgeç',
  codeTextFiles: 'Kod ve Metin Dosyaları',
  terminalMissing: 'Terminal bulunamadı',
  workspaceMissing: 'Workspace path eksik.',
  workspaceUntrusted: 'Bu klasör yetkili değil — klasörü uygulama içinden yeniden seçin.',
  writeRestricted: 'Dosya yalnızca seçili workspace içindeki kod/metin dosyalarına yazılabilir (.cs için Assets/Scripts altı).',
  fileNotRegular: 'Dosya düzenli bir dosya değil ya da birden fazla bağlantısı var.',
  fileOutside: 'Dosya workspace dışında.',
  fileExists: 'Dosya zaten var.',
  workspaceRootProtected: 'Proje klasörünün kendisi silinemez/taşınamaz.',
  fileMissing: 'Dosya bulunamadı',
  fileMissingPeriod: 'Dosya bulunamadı.',
  folderOutside: 'Klasör workspace dışında.',
  folderExists: 'Klasör zaten var.',
  invalidName: 'Geçersiz ad: yol ayracı içeremez.',
  targetOutside: 'Hedef workspace dışında.',
  nameExists: 'Bu isimde bir dosya zaten var.',
  trashFailed: 'Çöp kutusuna taşınamadı.',
  targetExists: 'Hedef konumda aynı isimde bir dosya var.',
} as const

export const EN: Record<keyof typeof TR, string> = {
  notePlaceholder: 'Add a comment…',
  noteLabel: 'Add a comment',
  noteLine: 'new line',
  noteCancel: 'cancel',
  noteSave: 'Save',
  noteLimit: 'At most {max} notes',
  noteSaveFailed: 'The note could not be saved, try again',
  modeInvalid: 'Invalid approval mode.',
  modeSendFailed: 'The approval mode could not be sent to the backend.',
  annotateBar: 'Annotate · click an element or draw an area · Esc to exit',
  updateRelease: 'Open release page',
  close: 'Close',
  updateFailedTitle: 'Update verification failed',
  updateFailedMessage: 'The new version’s signature could not be verified, so the update was not downloaded or installed.',
  updateFailedDetail: 'You can download the new version manually from the release page.',
  updateRestart: 'Restart now',
  updateLater: 'Later',
  updateReadyTitle: 'Update ready',
  updateReadyMessage: 'A new version is ready: v{version}',
  updateReadyDetail: 'Restart now to install the update. Choose "Later" to install it when you close the app.',
  authorizeLegacy: 'The app will be able to read and write files in this folder. You are seeing this prompt once because your previously selected workspace is missing from the new security registry. If you are unsure, choose Cancel and select the folder again in the app.',
  authorize: 'Authorize',
  cancel: 'Cancel',
  authorizeTitle: 'Authorize workspace folder',
  authorizeMessage: 'Allow access to this folder?',
  authorizeDetail: 'The app will be able to read and write files in this folder.',
  recycleBin: 'Recycle Bin',
  trash: 'Trash',
  trashMessage: 'The project folder "{name}" will be moved',
  trashDetail: '{path}\n\nIt will be moved to the {bin}. You can restore it from there.',
  trashGo: 'Move to {bin}',
  trashCancel: 'Cancel',
  codeTextFiles: 'Code and Text Files',
  terminalMissing: 'Terminal not found',
  workspaceMissing: 'Workspace path is missing.',
  workspaceUntrusted: 'This folder is not authorized — select it again in the app.',
  writeRestricted: 'Only code and text files in the selected workspace can be written (.cs files must be under Assets/Scripts).',
  fileNotRegular: 'The file is not a regular file or has multiple links.',
  fileOutside: 'The file is outside the workspace.',
  fileExists: 'The file already exists.',
  workspaceRootProtected: 'The project folder itself cannot be deleted or moved.',
  fileMissing: 'File not found',
  fileMissingPeriod: 'File not found.',
  folderOutside: 'The folder is outside the workspace.',
  folderExists: 'The folder already exists.',
  invalidName: 'Invalid name: path separators are not allowed.',
  targetOutside: 'The destination is outside the workspace.',
  nameExists: 'A file with this name already exists.',
  trashFailed: 'Could not move the file to the trash.',
  targetExists: 'A file with this name already exists at the destination.',
}

export function mt(key: keyof typeof TR, vars: Record<string, string | number> = {}): string {
  const text = (uiLanguage() === 'en' ? EN : TR)[key]
  return text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : placeholder)
}
