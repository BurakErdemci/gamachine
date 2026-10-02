import React, { useMemo, useState, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Search, Sparkles, Terminal, Puzzle, X } from 'lucide-react';
import { useLang } from '../../lib/i18n';

export interface CommandMeta {
  name: string;
  description?: string;
  argumentHint?: string;
  insert?: string;        // tıklanınca girdiye yazılacak metin (Codex skill'leri: defaultPrompt)
  displayName?: string;   // Codex skill'lerinin insanca adı (varsa name yerine gösterilir)
}

interface Props {
  meta: CommandMeta[];
  skills: string[];          // yetkili skill isim listesi (system/init'ten; cold-start'ta boş olabilir)
  provider?: string;         // 'claude' | 'codex' | ... — gösterim/insert davranışı için
  onSelect: (insertText: string) => void;
  onClose: () => void;
}

interface Group { key: string; label: string; icon: React.ReactNode; items: CommandMeta[]; }

/**
 * Açıklamalı, aranabilir komut & skill galerisi. Backend /slash-commands'ın `meta`
 * alanından beslenir (her komutun açıklaması get_server_info'dan gelir). Bir öğeye
 * tıklayınca '/<isim>' chat girdisine yazılır (argüman gerektiriyorsa kullanıcı
 * tamamlar) — yanlışlıkla yıkıcı komut tetiklenmesini önlemek için doğrudan
 * gönderilmez. Skill'ler ✨ ile en üstte; eklentiler prefix'e göre gruplanır.
 */
export const SkillsGallery: React.FC<Props> = ({ meta, skills, provider, onSelect, onClose }) => {
  const { t } = useLang();
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const isClaude = provider !== 'codex';  // Codex skill'leri '/' ile çağrılmaz (defaultPrompt/$ ile)

  useEffect(() => { inputRef.current?.focus(); }, []);

  const skillSet = useMemo(() => new Set(skills || []), [skills]);

  const groups = useMemo<Group[]>(() => {
    const q = query.trim().toLowerCase();
    const filtered = (meta || []).filter(c =>
      !q || c.name.toLowerCase().includes(q) || (c.description || '').toLowerCase().includes(q)
    );

    const skillItems: CommandMeta[] = [];
    const pluginMap: Record<string, CommandMeta[]> = {};
    const general: CommandMeta[] = [];

    for (const c of filtered) {
      if (skillSet.has(c.name)) { skillItems.push(c); continue; }
      const colon = c.name.indexOf(':');
      if (colon > 0) {
        const pfx = c.name.slice(0, colon);
        (pluginMap[pfx] ||= []).push(c);
      } else {
        general.push(c);
      }
    }

    const out: Group[] = [];
    if (skillItems.length) out.push({ key: 'skills', label: 'Skills', icon: <Sparkles size={12} className="text-[color:var(--focus)]" />, items: skillItems });
    Object.keys(pluginMap).sort().forEach(pfx =>
      out.push({ key: 'plugin:' + pfx, label: pfx, icon: <Puzzle size={12} className="text-[color:var(--focus)]" />, items: pluginMap[pfx] })
    );
    if (general.length) out.push({ key: 'general', label: t('skills.commands'), icon: <Terminal size={12} className="text-[color:var(--ink-dim)]" />, items: general });
    return out;
    // `t` bağımlılıkta: grup etiketi artık çeviriden geliyor ve dil değişince
    // memo yeniden hesaplanmazsa etiket eski dilde asılı kalır. Maliyeti yok —
    // `query` zaten her tuş vuruşunda memo'yu düşürüyor.
  }, [meta, skillSet, query, t]);

  const total = useMemo(() => groups.reduce((n, g) => n + g.items.length, 0), [groups]);

  return (
    <motion.div
      className="absolute left-4 right-4 bottom-full mb-2 bg-[color:var(--paper-raised)] rounded-xl z-50 shadow-2xl border border-[color:var(--paper-line)] overflow-hidden"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 6 }}
    >
      {/* Başlık + arama */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[color:var(--paper-line)] bg-[color:var(--paper-bg-2)]">
        <Sparkles size={13} className="text-[color:var(--focus)] shrink-0" />
        <span className="text-[12px] font-semibold text-[color:var(--ink-dim)] uppercase tracking-wider shrink-0">{t('skills.title')}</span>
        <div className="relative flex-1 ml-1">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-[color:var(--ink-faint)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('skills.search')}
            className="w-full bg-[color:var(--paper-sunk)] border border-[color:var(--paper-line)] rounded-md pl-7 pr-2 py-1 text-[12px] text-[color:var(--ink)] placeholder:text-[color:var(--ink-faint)] focus:outline-none focus:border-[color:var(--paper-line-strong)]"
          />
        </div>
        <button onClick={onClose} className="p-1 rounded text-[color:var(--ink-faint)] hover:text-[color:var(--ink)] hover:bg-[color:var(--paper-sunk)] transition-colors shrink-0">
          <X size={13} />
        </button>
      </div>

      <div className="max-h-[340px] overflow-y-auto custom-scrollbar py-1">
        {total === 0 ? (
          <div className="px-3 py-6 text-center text-[12px] text-[color:var(--ink-faint)]">{t('skills.noMatch')}</div>
        ) : (
          groups.map(g => (
            <div key={g.key} className="px-1 py-1">
              <div className="flex items-center gap-1.5 px-2 py-1">
                {g.icon}
                <span className="text-[12px] font-semibold uppercase tracking-wider text-[color:var(--ink-faint)]">{g.label}</span>
                <span className="text-[12px] text-[color:var(--ink-faint)]">({g.items.length})</span>
              </div>
              {g.items.map(c => {
                const needsArgs = !!(c.argumentHint && c.argumentHint.trim());
                const insertText = c.insert || ('/' + c.name + ' ');
                const label = isClaude ? '/' + c.name : (c.displayName || c.name);
                return (
                  <button
                    key={c.name}
                    onClick={() => onSelect(insertText)}
                    className="w-full text-left flex flex-col gap-0.5 px-2.5 py-1.5 rounded-lg hover:bg-[color:var(--paper-sunk)] transition-colors group"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="text-[12px] font-medium text-[color:var(--ink)]">{label}</span>
                      {needsArgs && (
                        <span className="text-[12px] text-[color:var(--accent-text)] font-mono">{c.argumentHint}</span>
                      )}
                    </div>
                    {c.description && (
                      <span className="text-[12px] text-[color:var(--ink-faint)] leading-snug line-clamp-2">{c.description}</span>
                    )}
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
    </motion.div>
  );
};
