/** The settings screen's pages, in rail order (mockup `?screen=ayarlar&set=<page>`). */
export const SETTINGS_PAGES = ['genel', 'modeller', 'gorunum', 'unity', 'onay', 'uzak', 'hesap'] as const;
export type SettingsPage = typeof SETTINGS_PAGES[number];

export function isSettingsPage(v: unknown): v is SettingsPage {
  return typeof v === 'string' && (SETTINGS_PAGES as readonly string[]).includes(v);
}
