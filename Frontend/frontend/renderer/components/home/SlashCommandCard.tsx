import React, { useMemo, useState } from 'react';
import { Gauge, Layers, Clock, ChevronDown, ChevronRight } from 'lucide-react';
import { MarkdownRenderer } from './MarkdownRenderer';
import { useLang } from '../../lib/i18n';
import { parseContextReport } from '../../lib/contextReport';

/**
 * Claude Code'un metin döndüren slash komutlarını düz markdown baloncuğu yerine
 * yapısal bir kart olarak gösterir:
 *   • /usage  → satır-bazlı: "X% used" → renkli progress bar, "Key: Value" →
 *               istatistik, gerisi → katlanır not. (Parse başarısızsa ham metne düşer.)
 *   • /context → markdown (tablo) döner; "**Tokens:** X / Y (Z%)" özetinden bir
 *               headline bar çıkarılır, tam markdown altta "Ayrıntı"da gösterilir.
 * NOT: `/cost` bu Claude Code sürümünde yok — session cost `/usage` içindedir.
 */

// The bar's fill level as a state the theme colours (thread.css `.slash-bar`), not a colour class.
function barLevel(p: number): 'high' | 'mid' | 'low' {
  if (p >= 80) return 'high';
  if (p >= 50) return 'mid';
  return 'low';
}

// ── /context: markdown özeti → headline bar + tam markdown ────────────────
const ContextCard: React.FC<{
  text: string;
  workspacePath?: string | null;
  onOpenFile?: (path: string) => void;
}> = ({ text, workspacePath, onOpenFile }) => {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const head = useMemo(() => parseContextReport(text), [text]);

  // Özet çıkmazsa ham markdown'a düş (tablolar zaten güzel render olur)
  if (!head) {
    return (
      <div className="msg-body">
        <MarkdownRenderer content={text} workspacePath={workspacePath} onOpenFile={onOpenFile} />
      </div>
    );
  }

  return (
    <div className="slash" data-slash="context">
      <div className="slash-head">
        <Layers size={14} aria-hidden="true" />
        <span className="slash-k">{t('slash.context')}</span>
        <span className="slash-cmd">/context</span>
      </div>
      <div className="slash-body">
        {head.model && <p className="slash-model">{head.model}</p>}
        <div>
          <div className="slash-row">
            <span className="slash-label">{head.used} / {head.total} token</span>
            <span className="slash-pct num">{head.pct}%</span>
          </div>
          <div className="slash-track">
            <div className="slash-bar" data-level={barLevel(head.pct)} style={{ width: `${Math.max(head.pct, 1)}%` }} />
          </div>
        </div>
        <button onClick={() => setOpen(v => !v)} className="slash-more">
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          <span>{t('slash.categoryBreakdown')}</span>
        </button>
        {open && (
          <div className="msg-body slash-detail">
            <MarkdownRenderer content={text} workspacePath={workspacePath} onOpenFile={onOpenFile} />
          </div>
        )}
      </div>
    </div>
  );
};

// ── /usage: satır-bazlı parser ────────────────────────────────────────────
interface ParsedBar { label: string; pct: number; reset?: string; }
interface ParsedStat { label: string; value: string; }
interface Parsed { subtitle: string; bars: ParsedBar[]; stats: ParsedStat[]; notes: string[]; }

function parseSlashOutput(text: string): Parsed {
  const lines = (text || '').split('\n').map(l => l.trim()).filter(Boolean);
  const bars: ParsedBar[] = [];
  const stats: ParsedStat[] = [];
  const notes: string[] = [];
  let subtitle = '';

  for (const line of lines) {
    // "Current session: 22% used · resets Jun 27, 11pm (Europe/Istanbul)"
    const pm = line.match(/^(.+?):\s*(\d+(?:\.\d+)?)%\s*used\b\s*(?:[·•-]\s*(resets?.+))?$/i);
    if (pm) {
      bars.push({ label: pm[1].trim(), pct: Math.min(100, parseFloat(pm[2])), reset: pm[3]?.trim() });
      continue;
    }
    // Genel "Label: değer" (URL değilse, çok uzun değilse)
    const kv = line.match(/^([A-Za-z$][\w .()\/$-]{0,38}):\s*(.+)$/);
    if (kv && !/^https?:\/\//i.test(kv[2]) && !kv[2].includes('://')) {
      stats.push({ label: kv[1].trim(), value: kv[2].trim() });
      continue;
    }
    // İlk açıklama satırı → alt başlık (bar/kv/soru olmayan ilk kısa satır;
    // Claude'da "You are currently using…", Codex'te "ChatGPT … Codex kullanımı")
    if (!subtitle && !line.endsWith('?') && line.length < 120) {
      subtitle = line;
      continue;
    }
    notes.push(line);
  }
  return { subtitle, bars, stats, notes };
}

interface Props {
  command: 'usage' | 'context' | string;
  text: string;
  workspacePath?: string | null;
  /** Ham markdown dallarındaki dosya linkleri için — verilmezse link tıklanınca
      hiçbir şey olmaz (navigasyon yine engellenir, bkz. MarkdownRenderer). */
  onOpenFile?: (path: string) => void;
}

export const SlashCommandCard: React.FC<Props> = ({ command, text, workspacePath, onOpenFile }) => {
  const { t } = useLang();
  if (command === 'context') return <ContextCard text={text} workspacePath={workspacePath} onOpenFile={onOpenFile} />;

  const { subtitle, bars, stats, notes } = useMemo(() => parseSlashOutput(text), [text]);
  const [notesOpen, setNotesOpen] = useState(false);

  // Hiçbir yapısal sinyal yoksa → ham metne düş (markdown)
  if (bars.length === 0 && stats.length === 0 && !subtitle) {
    return (
      <div className="msg-body">
        <MarkdownRenderer content={text} workspacePath={workspacePath} onOpenFile={onOpenFile} />
      </div>
    );
  }

  return (
    <div className="slash" data-slash="usage">
      {/* Başlık */}
      <div className="slash-head">
        <Gauge size={14} aria-hidden="true" />
        <span className="slash-k">{t('slash.usage')}</span>
        <span className="slash-cmd">/{command}</span>
      </div>

      <div className="slash-body">
        {subtitle && (
          <p className="slash-sub">{subtitle}</p>
        )}

        {/* Yüzde bar'ları */}
        {bars.length > 0 && (
          <div className="slash-bars">
            {bars.map((b, i) => (
              <div key={i}>
                <div className="slash-row">
                  <span className="slash-label">{b.label}</span>
                  <span className="slash-pct num">{b.pct}%</span>
                </div>
                <div className="slash-track">
                  <div
                    className="slash-bar"
                    data-level={barLevel(b.pct)}
                    style={{ width: `${Math.max(b.pct, 1)}%` }}
                  />
                </div>
                {b.reset && (
                  <div className="slash-reset">
                    <Clock size={12} aria-hidden="true" />
                    <span>{b.reset.replace(/^resets?\s*/i, t('slash.resetsPrefix'))}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Key: Value istatistikleri */}
        {stats.length > 0 && (
          <div className="slash-stats">
            {stats.map((s, i) => (
              <div key={i} className="slash-stat">
                <span className="slash-stat-k">{s.label}</span>
                <span className="slash-stat-v num">{s.value}</span>
              </div>
            ))}
          </div>
        )}

        {/* Detay notları (katlanır) */}
        {notes.length > 0 && (
          <div>
            <button
              onClick={() => setNotesOpen(v => !v)}
              className="slash-more"
            >
              {notesOpen ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
              <span>{t('slash.details', { sayi: notes.length })}</span>
            </button>
            {notesOpen && (
              <div className="slash-notes">
                {notes.map((n, i) => <p key={i}>{n}</p>)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
