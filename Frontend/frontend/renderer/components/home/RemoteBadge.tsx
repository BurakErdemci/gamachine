import { useEffect, useState } from "react";

import { useLang } from "../../lib/i18n";
import { remoteCall, type RemoteStatus } from "../../lib/remoteControl";

export const REMOTE_BADGE_POLL_MS = 15_000;

/** Remote control status for the always-visible badge. The settings section
 *  pushes what it reads through `setStatus`, so a toggle shows at once. */
export function useRemoteStatus(active: boolean, pollMs = REMOTE_BADGE_POLL_MS) {
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const tick = async () => {
      const res = await remoteCall<RemoteStatus>("status");
      if (!stopped && res.ok) setStatus(res.data);
    };
    void tick();
    const id = setInterval(() => { void tick(); }, pollMs);
    return () => { stopped = true; clearInterval(id); };
  }, [active, pollMs]);
  return { status, setStatus };
}

/** Shown while remote control is on: a phone may be watching and answering. */
export const RemoteBadge = ({ status, onClick }: { status: RemoteStatus | null; onClick?: () => void }) => {
  const { t } = useLang();
  if (!status?.enabled) return null;
  const state = status.connected ? t("remote.status.connected")
    : status.gave_up || status.last_error ? t("remote.status.error", { hata: status.last_error || "" })
      : t("remote.status.connecting");
  const dot = status.connected ? "bg-emerald-400"
    : status.gave_up || status.last_error ? "bg-red-500" : "bg-yellow-400 animate-pulse";
  return (
    <button
      type="button"
      data-testid="remote-badge"
      onClick={onClick}
      title={t("remote.badgeTitle", { durum: state })}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] text-[10px] font-semibold text-slate-400 whitespace-nowrap hover:bg-white/[0.06]"
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      📱 {t("remote.badge")}
    </button>
  );
};
