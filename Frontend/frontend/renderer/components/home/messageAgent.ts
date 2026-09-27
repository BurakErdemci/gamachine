import { CLI_GROUPS, CLOUD_PROVIDER_META } from './ModelSelector';
import { shortModelId, stripBidi } from '../../lib/modelText';

export interface MessageAgent {
  /** Agent display name, as the model selector shows it ("Codex", "OpenCode"). */
  name: string;
  /** `ModelAvatar` brand key. */
  brand: string;
  /** Short model id, or null when the message did not record one. */
  model: string | null;
}

// API providers the selector's cloud groups do not list.
const EXTRA_API_LABELS: Record<string, string> = { openrouter: 'OpenRouter', ollama: 'Ollama' };

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * The agent that wrote an assistant message, from the message's own stored
 * `provider`/`model` (Burak, 27 Sep 2026). Null when the message has none: a
 * row stored before the columns existed must show no model rather than the
 * chat's current one, which is what relabelled OpenCode answers as Codex.
 *
 * `provider` is the backend's agent family, which is the selector group's
 * `availKey` for CLIs and `api-<provider_type>` for API loops.
 */
export const messageAgent = (provider?: string | null, model?: string | null): MessageAgent | null => {
  const p = (provider || '').toLowerCase();
  if (!p) return null;
  let name: string;
  let brand: string;
  if (p.startsWith('api-')) {
    const key = p.slice(4);
    name = CLOUD_PROVIDER_META[key]?.label || EXTRA_API_LABELS[key] || capitalize(key);
    brand = key;
  } else {
    const group = CLI_GROUPS.find(g => g.availKey === p);
    name = group?.label || capitalize(p);
    brand = group?.brand || p;
  }
  // Sanitised because this is a label: model ids come from provider
  // catalogues, and a directional override would reorder the name the user
  // reads while the stored id stays what it is.
  return { name: stripBidi(name), brand, model: model ? stripBidi(shortModelId(model)) : null };
};
