/**
 * The provider groups the model menu and the settings screen both list. One copy: the menu and
 * the "Modeller ve hesaplar" page must name, order and detect the same groups.
 */
export interface CliGroupDef {
  key: string;
  label: string;
  brand: string;               // ModelAvatar/ModelLogo marka anahtarı
  availKey: string;            // /cli-availability yanıtındaki anahtar
  cliLabel: string;            // "kurulu değil" uyarısında insan-okur ad
  matches: (id: string) => boolean;
  dynamic?: 'cursor' | 'opencode' | 'copilot' | 'codex'; // /cli-models/{cli} ile liste
  accent: string;              // aktif model rengi (tailwind text sınıfı)
  dot: string;                 // aktif nokta rengi (tailwind bg sınıfı)
  badge?: string;              // grup başlığı yanındaki küçük rozet
}

export const CLI_GROUPS: CliGroupDef[] = [
  {
    key: 'claude', label: 'Claude Code', brand: 'claude', availKey: 'claude', cliLabel: 'Claude Code',
    matches: id => id.startsWith('claude-'),
    accent: 'text-orange-400', dot: 'bg-orange-400',
  },
  {
    key: 'codex', label: 'Codex', brand: 'openai', availKey: 'codex', cliLabel: 'Codex',
    matches: id => id.startsWith('gpt-'),
    dynamic: 'codex',
    accent: 'text-emerald-400', dot: 'bg-emerald-400',
  },
  {
    key: 'gemini', label: 'Antigravity', brand: 'gemini', availKey: 'agy', cliLabel: 'Antigravity (agy)',
    matches: id => id.startsWith('gemini') || id.startsWith('agy-'),
    accent: 'text-blue-400', dot: 'bg-blue-400',
  },
  {
    key: 'copilot', label: 'GitHub Copilot', brand: 'copilot', availKey: 'copilot', cliLabel: 'GitHub Copilot CLI',
    matches: id => id.startsWith('copilot-'),
    dynamic: 'copilot',
    accent: 'text-violet-300', dot: 'bg-violet-300',
  },
  {
    key: 'cursor', label: 'Cursor', brand: 'cursor', availKey: 'cursor', cliLabel: 'Cursor CLI (agent)',
    matches: id => id.startsWith('cursor-'),
    dynamic: 'cursor',
    accent: 'text-slate-100', dot: 'bg-slate-100',
  },
  {
    key: 'opencode', label: 'OpenCode', brand: 'opencode', availKey: 'opencode', cliLabel: 'OpenCode',
    matches: id => id.startsWith('opencode:'),
    dynamic: 'opencode',
    accent: 'text-teal-300', dot: 'bg-teal-300',
    badge: 'FREE + GO',
  },
  {
    key: 'kimi', label: 'Kimi Code', brand: 'moonshot', availKey: 'kimi', cliLabel: 'Kimi Code CLI',
    matches: id => id.startsWith('kimi-'),
    accent: 'text-fuchsia-300', dot: 'bg-fuchsia-300',
  },
];

export type ModelItem = { id: string; name: string; provider?: string; openrouter_id?: string; disabled?: boolean; disabled_reason?: string; available?: boolean; verified?: boolean; source?: 'live' | 'openrouter'; context_length?: number };
export type CliDoctor = Record<string, { installed: boolean; loggedIn: boolean | null }>;

// Bulut API sağlayıcı grupları (abonelik CLI grupları gibi kategorize görünüm)
export const CLOUD_PROVIDER_META: Record<string, { label: string; badge?: string }> = {
  anthropic:  { label: 'Anthropic' },
  openai:     { label: 'OpenAI' },
  google:     { label: 'Google' },
  deepseek:   { label: 'DeepSeek' },
  groq:       { label: 'Groq' },
  moonshot:   { label: 'Moonshot' },
  'z-ai':     { label: 'Z.ai' },
  nvidia:     { label: 'NVIDIA NIM', badge: 'provider.badge.free' },
};


/**
 * Providers that run on the user's own API key (the old settings modal's provider tiles,
 * minus the subscription and Ollama ones). `badge` is a dictionary key.
 */
export const API_KEY_PROVIDERS: { value: string; label: string; badge?: string }[] = [
  { value: 'anthropic',  label: 'Anthropic' },
  { value: 'openai',     label: 'OpenAI' },
  { value: 'google',     label: 'Google' },
  { value: 'deepseek',   label: 'DeepSeek' },
  { value: 'moonshot',   label: 'Kimi' },
  { value: 'z-ai',       label: 'GLM' },
  { value: 'nvidia',     label: 'NVIDIA', badge: 'provider.badge.free' },
  { value: 'groq',       label: 'Groq' },
  { value: 'openrouter', label: 'OpenRouter' },
];

/**
 * Which menu provider the current pick belongs to: a CLI group key, `cloud:<provider>`, or
 * `local`. The model id decides first (a subscription CLI runs every family).
 */
export function activeProviderKey(providerType: string, modelName: string): string | null {
  const group = CLI_GROUPS.find(g => g.matches(modelName || ''));
  if (group && providerType === 'subscription') return group.key;
  if (providerType === 'ollama') return 'local';
  if (providerType && providerType !== 'subscription') return `cloud:${providerType}`;
  return group?.key ?? null;
}

/**
 * The chip's model name: the catalog name without what the mark already says. "Claude Opus 5.5
 * (CLI)" -> "Opus 5.5", "Codex (GPT-6 Sol)" -> "GPT-6 Sol". Display only; ids are never touched.
 */
export function chipModelName(name: string, markIsClaude = false): string {
  let s = (name || '').trim();
  const codex = /^Codex \((.+)\)$/.exec(s);
  if (codex) s = codex[1];
  s = s.replace(/\s*\(CLI\)$/i, '');
  // Only under the Claude mark: under Antigravity's mark "Claude" is the information.
  if (markIsClaude) s = s.replace(/^Claude (?=[A-Za-z])/, '');
  return s || name;
}
