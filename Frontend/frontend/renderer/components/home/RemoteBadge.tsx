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
  // The mockup's lamps: live = ok, failed = the alert accent, still trying = the running pulse.
  const lamp = status.connected ? "status-ok"
    : status.gave_up || status.last_error ? "status-err" : "status-running";
  return (
    <button
      type="button"
      data-testid="remote-badge"
      onClick={onClick}
      title={t("remote.badgeTitle", { durum: state })}
      className="bar-chip"
    >
      <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="6" y="2.5" width="8" height="15" rx="1.6" />
        <path d="M9 15h2" />
      </svg>
      <span className="bar-chip-label">{t("remote.badge")}</span>
      <span className={`status ${lamp}`} aria-hidden="true" />
    </button>
  );
};
