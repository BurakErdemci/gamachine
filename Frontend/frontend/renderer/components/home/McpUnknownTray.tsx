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
import { AlertTriangle } from 'lucide-react';
import { McpTrayGate, unityOzeti } from '../../hooks/home/useMCPApproval';
import { postMcpDecision, decisionToast } from '../../hooks/home/gateResponse';
import { stripBidi } from '../../lib/modelText';
import { useLang } from '../../lib/i18n';
import { RiskReasonLine } from './RiskReasonLine';
import { ApprovalCard, CheckIcon } from './ApprovalCard';

interface McpUnknownTrayProps {
  gates: McpTrayGate[];
  apiBase: string;
  sessionToken: string;
  showToast: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
  /** A paired phone can decide these: unowned gates are cards too, and the phone lists cards
   *  that belong to no chat (relay/public/app.js renderCards). */
  phonePaired?: boolean;
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

export const McpUnknownTray: React.FC<McpUnknownTrayProps> = ({ gates, apiBase, sessionToken, showToast, phonePaired }) => {
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
      className="tray custom-scrollbar"
    >
      <div className="tray-col">
        <div className="tray-head">
          <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
          <span className="tray-title">{t('mcp.trayTitle')}</span>
          {visible.length > 1 && (
            <span className="tray-count">{t('mcp.trayCount', { sayi: visible.length })}</span>
          )}
        </div>
        <p className="tray-hint">{t('mcp.trayHint')}</p>
        {visible.map(g => {
          const target = targetOf(g.params);
          const locked = busy.has(g.gateId);
          return (
            <ApprovalCard
              key={g.gateId}
              kind="tray"
              testId={`mcp-tray-${g.gateId}`}
              who={t('card.whoUnity')}
              name={t('card.nameUnity')}
              why={<RiskReasonLine reason={g.riskReason} detail={g.riskDetail} />}
              phoneHint={phonePaired}
              body={(
                <>
                  <dl className="tray-facts">
                    <dt>{t('mcp.trayTool')}</dt>
                    <dd className="mono">{stripBidi(g.tool)}</dd>
                    <dt>{t('mcp.trayTarget')}</dt>
                    <dd className="mono">{target ? stripBidi(target) : t('mcp.trayTargetUnknown')}</dd>
                    <dt>{t('mcp.trayProject')}</dt>
                    <dd className="mono is-dim">{g.workspacePath ? stripBidi(g.workspacePath) : t('mcp.sourceUnknown')}</dd>
                  </dl>
                  {/* The full parameters, as the in-chat card shows them: the tool
                      name and target alone do not say what is being approved. */}
                  <pre className="approval-cmd custom-scrollbar"><code>{stripBidi(unityOzeti(g.tool, g.params))}</code></pre>
                </>
              )}
              actions={(
                <>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => { void decide(g.gateId, true); }}
                    className="btn btn-primary"
                  >
                    <CheckIcon /><span>{t('mcp.trayApprove')}</span>
                  </button>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => { void decide(g.gateId, false); }}
                    className="btn btn-ghost"
                  >
                    {t('mcp.trayDeny')}
                  </button>
                </>
              )}
            />
          );
        })}
      </div>
    </section>
  );
};
