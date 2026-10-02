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

/** Shown while remote control is on: a phone may be watching and answering. It sits in the
 *  sidebar foot beside Settings as an icon with a lamp (mockup `.foot-phone`); the name and
 *  state live in the tooltip. A text chip in the top bar read as clutter (Burak, 2 Oct). */
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
      data-guide="phone-status"
      onClick={onClick}
      title={t("remote.badgeTitle", { durum: state })}
      aria-label={t("remote.badgeTitle", { durum: state })}
      className="foot-btn foot-phone"
    >
      <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="6" y="2.5" width="8" height="15" rx="1.6" />
        <path d="M9 15h2" />
      </svg>
      <span className={`status ${lamp}`} aria-hidden="true" />
    </button>
  );
};
