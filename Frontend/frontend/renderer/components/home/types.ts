export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  openrouter_id?: string;
  paid?: boolean;
  // Hesabın gerçekten erişip erişmediği. TANIMSIZ üçüncü bir hâl: sağlayıcının
  // listesi alınamadı. `false` ile karıştırılmamalı — ağı olmayan bir makinede
  // çalışan her modeli "erişemiyorsun" diye göstermek yanlış kırmızıdır.
  available?: boolean;
  // Satır kullanıcının KENDİ anahtarıyla doğrulandı mı. `false` → OpenRouter'ın
  // açık kataloğundan geliyor, yani "böyle bir model var" ama "senin hesabında
  // var" DEĞİL. İkisi ayrı iddia.
  verified?: boolean;
  // 'live' sağlayıcının kendi listesinden, 'openrouter' açık katalogdan.
  source?: 'live' | 'openrouter';
  // OpenRouter kataloğundan gelen bağlam penceresi (token).
  context_length?: number;
}

export interface AvailableModels {
  local: ModelInfo[];
  cloud: ModelInfo[];
  subscription: ModelInfo[];
  // saglayici -> 'live' | 'unknown' | 'no_key'
  cloud_sources?: Record<string, string>;
}

export interface UserData {
  id: number;
  name: string;
  sessionToken: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  // Branches point at their family's root (the backend flattens nesting);
  // roots carry null. Absent on an older backend: every chat is then a root.
  parent_id?: number | null;
  hidden?: boolean;
}

// Chat'teki araç chip'i: args = araç girdisi (PARAMETRELER), output = araç sonucu (ÇIKTI),
// id = tool_use_id (sonucu doğru chip'e bağlamak için).
export interface ToolCallEntry {
  tool: string;
  args?: any;
  summary?: string;
  success?: boolean;
  output?: string;
  id?: string;
}

/**
 * A non-error notice drawn under a message.
 *
 * Why a separate field instead of appending to `content`: `content` is the
 * model's own text, rendered as markdown — a warning written there reads as
 * something the model said, and a detail block cannot be collapsed inside it.
 * Why not the error branch: the two `❌` paths (`chat.errorOccurred` and the
 * `error` event) mean the turn failed. Here the run worked; it was either cut
 * short or a side pipeline broke. Giving both the same look makes users read a
 * capped run as a crash.
 *
 * Texts are stored ALREADY TRANSLATED (same as `content`): switching language
 * later leaves old notices in the old language.
 */
export interface MessageNotice {
  /** `warning` = the run continued but something failed. `stopped` = the run ended early. */
  kind: 'warning' | 'stopped';
  title: string;
  message: string;
  /** Optional technical detail, shown in a collapsed disclosure. */
  detail?: string;
}

export interface Message {
  id: number;
  /**
   * `system` = AUTO-WAKE row: a turn the client started by itself when a
   * background task finished, NOT something the user typed. It is a separate
   * role because attributing it to `user` would put words in the user's mouth
   * — on screen (a blue user bubble) and in the CLI handoff transcript, where
   * the backend deliberately skips system rows.
   */
  role: 'user' | 'assistant' | 'system';
  content: string;
  smells: any[];
  timestamp: string;
  provider?: string;
  is_refined?: boolean;
  thinking?: string | null;
  thinking_duration_ms?: number | null;
  tool_calls?: ToolCallEntry[];
  tools?: ToolCallEntry[];
  images?: string[];
  slashCommand?: string;  // 'usage' | 'cost' — özel kart olarak render edilen slash komutu yanıtı
  // Tur sonu istatistiği (backend turn_usage event'i) — mesaj altında küçük özet satırı
  usage?: { output_tokens?: number; duration_ms?: number | null };
  notices?: MessageNotice[];
}

// Kalıcı bağlam göstergesinin verisi. `percent` bir ÖLÇÜM DEĞİL: yalnız DB'ye
// yazılan mesaj metnini sayıyor, araç çıktılarını ve sistem promptunu görmüyor.
// `estimated` bu yüzden var ve arayüz onu gizlemiyor.
//
// `last_turn` yalnız gerçek token üreten yollarda dolu: doğrudan API çağıran üç
// sağlayıcı ve Claude Code. Codex, agy, cursor, copilot, opencode ve kimi hiç
// token bildirmiyor — orada alan BULUNMUYOR, sıfır değil.
export interface ContextUsage {
  percent: number;
  should_compact: boolean;
  message_count: number;
  estimated?: boolean;
  last_turn?: { input_tokens?: number; output_tokens?: number; cost_usd?: number | null };
  // `/context` raporundan gelen GERÇEK doluluk. Dolduğunda `estimated` false
  // olur ve gösterge "~" işaretini bırakır — işaret tahmin olduğunu söylüyor,
  // gerçek sayının üstünde durursa yalan söyler.
  real?: { used: string; total: string; model?: string };
}

// Canlı aktivite göstergesi (backend status event'leri): Claude'un o an ne yaptığı +
// tur boyunca üretilen token. loading sırasında ChatPanel'de spinner satırı olarak görünür.
export interface ChatActivity {
  detail: string;
  tokens?: number;
}

export interface FileEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  extension: string;
}

export interface AIConfig {
  provider_type: string;
  api_key: string;
  model_name: string;
  thinking_level: 'low' | 'medium' | 'high' | 'off';
  has_key?: boolean;
}

/** `/provider-ready` yanıtı — sohbet kapısının girdisi.
 *
 * `needs` bilerek bir KOD, cümle değil: backend'in çeviri mekanizması yok ve
 * oraya metin koymak ~300 sabit Türkçe metinlik borcu büyütürdü. Kullanıcıya
 * görünen metin `i18n.tsx`'teki `gate.needs.*` anahtarlarından geliyor.
 */
export interface ProviderReady {
  ready: boolean;
  kind: 'api' | 'cli' | 'local';
  provider: string;
  needs: null | 'apikey' | 'install' | 'login' | 'service';
}

export interface ExportFileEntry {
  name: string;
  code: string;
  path: string;
}

export interface ExportModalState {
  isOpen: boolean;
  codeString: string;
  suggestedName: string;
  targetDir: string;
  existingFile: boolean;
  multiFile: boolean;
  files: ExportFileEntry[];
  exportResult: { success: boolean; message: string } | null;
}

export type GenerationMode = 'auto' | 'step';
