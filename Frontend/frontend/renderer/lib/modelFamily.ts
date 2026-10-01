/** The mockup's model colours (--model-claude / -codex / -gemini / -other in tokens.css). */
export type ModelFamily = 'claude' | 'codex' | 'gemini' | 'other';

/**
 * Which colour family the current pick belongs to. Same precedence as home.tsx's brand light:
 * the model id decides first (a subscription CLI runs every family), the provider second.
 */
export function modelFamily(modelName?: string | null, provider?: string | null): ModelFamily {
  const m = (modelName || '').toLowerCase();
  if (m.startsWith('claude-')) return 'claude';
  if (m.startsWith('gpt-') || m.startsWith('codex')) return 'codex';
  if (m.startsWith('gemini') || m.startsWith('agy-')) return 'gemini';
  const p = (provider || '').toLowerCase();
  if (p === 'claude' || p === 'anthropic') return 'claude';
  if (p === 'openai' || p === 'codex') return 'codex';
  if (p === 'gemini' || p === 'google') return 'gemini';
  return 'other';
}
