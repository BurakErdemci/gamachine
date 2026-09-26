/**
 * McpUnknownTray — Unity MCP requests whose source chat the backend could not
 * name (`conversation_id: null` in `/mcp-pending`).
 *
 * Such a request belongs to no chat, so it is drawn outside all of them and
 * stays put when the user switches chats. Showing it inside the open chat
 * would present another chat's (or an outside client's) action as part of
 * this conversation. Each entry decides itself through `postMcpDecision`, the
 * same path the in-chat cards use; nothing here approves on its own.
 */
import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, X } from 'lucide-react';
import { McpTrayGate, unityOzeti } from '../../hooks/home/useMCPApproval';
import { postMcpDecision, decisionToast } from '../../hooks/home/gateResponse';
import { stripBidi } from '../../lib/modelText';
import { useLang } from '../../lib/i18n';

interface McpUnknownTrayProps {
  gates: McpTrayGate[];
  apiBase: string;
  sessionToken: string;
  showToast: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
}

/** What the request acts on: the Unity project first, then a path or command. */
const targetOf = (params: any): string | null => {
  if (!params || typeof params !== 'object') return null;
  for (const key of ['unity_instance', 'path', 'command']) {
    const v = params[key];
    if (typeof v === 'string' && v) return v;
  }
  return null;
};

export const McpUnknownTray: React.FC<McpUnknownTrayProps> = ({ gates, apiBase, sessionToken, showToast }) => {
  const { t } = useLang();
  // Written synchronously so a double click cannot send two decisions.
  const inFlightRef = useRef<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  // Decided here and delivered; hidden until the next poll drops the entry.
  // A failed delivery is not hidden: the entry stays decidable if it is still
  // pending, and the poll removes it if it is not.
  const [sent, setSent] = useState<Set<string>>(new Set());

  useEffect(() => {
    const live = new Set(gates.map(g => g.gateId));
    setSent(prev => {
      const kept = [...prev].filter(id => live.has(id));
      return kept.length === prev.size ? prev : new Set(kept);
    });
  }, [gates]);

  const visible = gates.filter(g => !sent.has(g.gateId));
  if (visible.length === 0) return null;

  const decide = async (gateId: string, approved: boolean) => {
    if (inFlightRef.current.has(gateId)) {
      showToast(t('mcp.suppressedInFlight'), 'warning');
      return;
    }
    inFlightRef.current.add(gateId);
    setBusy(new Set(inFlightRef.current));
    try {
      const failure = await postMcpDecision(apiBase, gateId, approved, sessionToken);
      const note = decisionToast(failure, t(approved ? 'mcp.trayApproved' : 'mcp.trayDenied'));
      showToast(note.message, note.type);
      if (!failure) setSent(prev => new Set(prev).add(gateId));
    } finally {
      inFlightRef.current.delete(gateId);
      setBusy(new Set(inFlightRef.current));
    }
  };

  return (
    <section
      data-testid="mcp-unknown-tray"
      aria-label={t('mcp.trayTitle')}
      className="shrink-0 max-h-[45vh] overflow-y-auto custom-scrollbar border-b border-amber-500/20 bg-amber-950/10 px-3 py-2 space-y-2"
    >
      <div className="flex items-center gap-2 text-amber-400 text-[11px] font-bold uppercase tracking-wider">
        <AlertTriangle size={13} className="shrink-0" />
        <span className="truncate">{t('mcp.trayTitle')}</span>
        {visible.length > 1 && (
          <span className="ml-auto shrink-0 font-semibold normal-case tracking-normal text-amber-300/80">
            {t('mcp.trayCount', { sayi: visible.length })}
          </span>
        )}
      </div>
      <p className="text-[10.5px] text-slate-500">{t('mcp.trayHint')}</p>
      {visible.map(g => {
        const target = targetOf(g.params);
        const locked = busy.has(g.gateId);
        return (
          <div
            key={g.gateId}
            data-testid={`mcp-tray-${g.gateId}`}
            className="rounded-lg border border-amber-500/30 bg-black/40 p-2.5 text-[11px]"
          >
            <dl className="grid grid-cols-[auto,1fr] gap-x-2 gap-y-0.5 mb-2">
              <dt className="text-slate-500">{t('mcp.trayTool')}</dt>
              <dd className="font-mono text-slate-200 truncate">{stripBidi(g.tool)}</dd>
              <dt className="text-slate-500">{t('mcp.trayTarget')}</dt>
              <dd className="font-mono text-slate-200 truncate">
                {target ? stripBidi(target) : t('mcp.trayTargetUnknown')}
              </dd>
              <dt className="text-slate-500">{t('mcp.trayProject')}</dt>
              <dd className="font-mono text-slate-400 truncate">
                {g.workspacePath ? stripBidi(g.workspacePath) : t('mcp.sourceUnknown')}
              </dd>
            </dl>
            {/* The full parameters, as the in-chat card shows them: the tool
                name and target alone do not say what is being approved. */}
            <pre className="max-h-32 overflow-y-auto custom-scrollbar whitespace-pre-wrap break-all rounded bg-black/50 border border-white/5 px-2 py-1.5 text-[10.5px] text-emerald-400 font-mono">
              {stripBidi(unityOzeti(g.tool, g.params))}
            </pre>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                disabled={locked}
                onClick={() => { void decide(g.gateId, true); }}
                className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white font-bold transition-colors"
              >
                <Check size={12} className="stroke-[3px]" /> {t('mcp.trayApprove')}
              </button>
              <button
                type="button"
                disabled={locked}
                onClick={() => { void decide(g.gateId, false); }}
                className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-300 font-bold transition-colors"
              >
                <X size={12} /> {t('mcp.trayDeny')}
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
};
