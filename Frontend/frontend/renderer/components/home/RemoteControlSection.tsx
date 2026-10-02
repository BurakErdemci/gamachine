import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { create as createQr } from "qrcode";

import { useLang } from "../../lib/i18n";
import { stripBidi } from "../../lib/modelText";
import { confirmDialog } from "../ui/ConfirmDialog";
import {
  remoteCall, remoteErrorKey,
  type PairOffer, type PendingPair, type RemoteDevice, type RemoteResult, type RemoteStatus,
} from "../../lib/remoteControl";
import { Chev, Lamp, SetCard, SetGroup, SetRow, SetSwitch } from "./settings/controls";

/** One SVG path for the dark modules, built in memory: nothing is fetched and
 *  no markup string is injected. */
export function qrPath(text: string): { size: number; d: string } {
  const qr = createQr(text, { errorCorrectionLevel: "M" });
  const size = qr.modules.size;
  let d = "";
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (qr.modules.get(y, x)) d += `M${x} ${y}h1v1h-1z`;
    }
  }
  return { size, d };
}

const QrCode = ({ text }: { text: string }) => {
  const { size, d } = qrPath(text);
  const quiet = 4;
  const full = size + quiet * 2;
  return (
    <svg data-testid="remote-qr" viewBox={`${-quiet} ${-quiet} ${full} ${full}`}
      shapeRendering="crispEdges" role="img" aria-label="QR">
      <rect x={-quiet} y={-quiet} width={full} height={full} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
};

const PENDING_POLL_MS = 1500;
const STATUS_POLL_MS = 3000;

const mmss = (ms: number) => {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

const fmtDate = (ms: number | null, lang: string) =>
  ms ? new Date(ms).toLocaleString(lang === "tr" ? "tr-TR" : "en-GB") : "";

interface Props {
  /** Every status this section reads, so the always-visible badge follows at once. */
  onStatus?: (status: RemoteStatus) => void;
  statusPollMs?: number;
  pendingPollMs?: number;
  /** Called after a change the screen confirms with its "Saved" note. */
  onSaved?: () => void;
}

export const RemoteControlSection = ({ onStatus, statusPollMs = STATUS_POLL_MS, pendingPollMs = PENDING_POLL_MS, onSaved }: Props) => {
  const { t, lang } = useLang();
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [devices, setDevices] = useState<RemoteDevice[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [offer, setOffer] = useState<PairOffer | null>(null);
  const [pending, setPending] = useState<PendingPair | null>(null);
  const [expired, setExpired] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [relayInput, setRelayInput] = useState("");
  const [needsRepair, setNeedsRepair] = useState(false);
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  const applyStatus = useCallback((s: RemoteStatus) => {
    setStatus(s);
    onStatusRef.current?.(s);
  }, []);

  const refresh = useCallback(async () => {
    const s = await remoteCall<RemoteStatus>("status");
    if (s.ok) applyStatus(s.data);
    const d = await remoteCall<{ devices: RemoteDevice[] }>("devices");
    if (d.ok && Array.isArray(d.data?.devices)) setDevices(d.data.devices);
  }, [applyStatus]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); }, statusPollMs);
    return () => clearInterval(id);
  }, [refresh, statusPollMs]);

  const run = useCallback(async (action: string, arg?: unknown): Promise<RemoteResult> => {
    setBusy(action);
    setError(null);
    setNote(null);
    const res = await remoteCall(action, arg);
    // `=== false`: with `strict: false` a truthiness check does not narrow the union.
    if (res.ok === false) setError(t(remoteErrorKey(res.code), { kod: res.code }));
    setBusy(null);
    return res;
  }, [t]);

  // ── pairing: countdown and the pending request ──────────────────────────
  useEffect(() => {
    if (!offer) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [offer]);

  useEffect(() => {
    if (offer && !pending && now >= offer.expires_at) {
      setOffer(null);
      setExpired(true);
    }
  }, [offer, pending, now]);

  useEffect(() => {
    if (!offer && !pending) return;
    let stopped = false;
    const tick = async () => {
      const res = await remoteCall<{ pending: PendingPair | null }>("pair-pending");
      if (stopped || !res.ok) return;
      const next = res.data?.pending ?? null;
      // A request that vanished unanswered expired; its QR secret was
      // single-use, so the code on screen is dead too.
      if (pending && !next) {
        setOffer(null);
        setExpired(true);
      }
      setPending(next);
    };
    void tick();
    const id = setInterval(() => { void tick(); }, pendingPollMs);
    return () => { stopped = true; clearInterval(id); };
  }, [offer, pending, pendingPollMs]);

  const startPairing = async () => {
    setExpired(false);
    setPending(null);
    const res = await run("pair-start");
    if (res.ok) {
      setNow(Date.now());
      setOffer({ qr_url: res.data.qr_url, expires_at: res.data.expires_at });
    }
  };

  const cancelPairing = async () => {
    if (pending) await remoteCall("pair-reject");
    setOffer(null);
    setPending(null);
  };

  const approve = async () => {
    const res = await run("pair-approve");
    if (res.ok) {
      setNote(t("remote.paired", { cihaz: stripBidi(res.data?.device?.name || "") || t("chat.phoneUnnamed") }));
      setOffer(null);
      setPending(null);
      void refresh();
    } else if (res.ok === false && (res.code === "no_pending_pairing" || res.code === "phone_left")) {
      setPending(null);
    }
  };

  const reject = async () => {
    const res = await run("pair-reject");
    if (res.ok) {
      setOffer(null);
      setPending(null);
      setNote(t("remote.pairRejected"));
    }
  };

  // ── settings ────────────────────────────────────────────────────────────
  const toggle = async () => {
    const turningOn = !status?.enabled;
    const res = await run(turningOn ? "enable" : "disable");
    if (res.ok) {
      applyStatus(res.data);
      if (!turningOn) { setOffer(null); setPending(null); }
      onSaved?.();
    }
  };

  const saveRelay = async (url: string | null) => {
    const res = await run("set-relay-url", url);
    if (res.ok) {
      setNeedsRepair(!!res.data?.devices_need_repair);
      setNote(t("remote.relaySaved"));
      if (url === null) setRelayInput("");
      onSaved?.();
      void refresh();
    }
  };

  const setKeepAwake = async (on: boolean) => {
    const res = await run("set-keep-awake", on);
    if (res.ok) { void refresh(); onSaved?.(); }
  };

  const removeDevice = async (device: RemoteDevice) => {
    const name = stripBidi(device.name) || t("chat.phoneUnnamed");
    if (!(await confirmDialog(t("remote.removeConfirm", { cihaz: name }), t("remote.remove"), t("confirm.cancel")))) return;
    const res = await run("remove-device", device.device_id);
    if (res.ok === true || (res.ok === false && res.code === "unknown_device")) void refresh();
  };

  const removeAll = async () => {
    if (!(await confirmDialog(t("remote.removeAllConfirm"), t("remote.removeAll"), t("confirm.cancel")))) return;
    const res = await run("remove-all-devices");
    if (res.ok) void refresh();
  };

  const forget = async () => {
    if (!(await confirmDialog(t("remote.forgetConfirm"), t("remote.forgetConfirmButton"), t("confirm.cancel")))) return;
    const res = await run("forget");
    if (res.ok) {
      applyStatus(res.data);
      setOffer(null);
      setPending(null);
      setDevices([]);
      setNeedsRepair(false);
      setNote(t(res.data?.relay_reset ? "remote.forgotten" : "remote.forgottenRelayKept"));
    }
  };

  const enabled = !!status?.enabled;
  const statusText = !status ? "…"
    : !status.enabled ? t("remote.status.off")
      : status.connected ? t("remote.status.connected")
        : status.gave_up ? t("remote.status.gaveUp")
          : status.last_error ? t("remote.status.error", { hata: status.last_error })
            : t("remote.status.connecting");
  const dotTone = !status?.enabled ? "off"
    : status.connected ? "ok"
      : status.gave_up || status.last_error ? "danger" : "busy";

  const phoneIc = (
    <svg className="ic set-dev-ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="6" y="2.5" width="8" height="15" rx="1.6" /><path d="M9 15h2" /></svg>
  );

  return (
    <div data-testid="remote-section">
      {/* On / off + keep awake */}
      <SetGroup>
        <SetCard>
          <div className="set-row set-row-hero" data-guide="settings-remote">
            {phoneIc}
            <div className="set-rt">
              <p className="set-name">{t("remote.title")}</p>
              <div className="set-hint">
                <span data-testid="remote-status-lamp" data-tone={dotTone}
                  className={`lamp-dot${dotTone === "ok" ? " is-ok" : dotTone === "danger" ? " is-warn" : dotTone === "busy" ? " is-busy" : ""}`} />
                <b data-testid="remote-status" className="set-state">{statusText}</b>
                {status?.enabled && status.connected && status.phones_online > 0 && (
                  <> · {t("remote.status.phonesOnline", { sayi: status.phones_online })}</>
                )}
              </div>
            </div>
            <SetSwitch checked={enabled} label={t("remote.title")} testId="remote-toggle" onToggle={toggle}
              disabled={!status || busy === "enable" || busy === "disable"} />
          </div>
          <SetRow
            name={t("remote.keepAwake")}
            hint={t("remote.keepAwakeHint")}
            control={<SetSwitch checked={!!status?.keep_awake} label={t("remote.keepAwake")} testId="remote-keep-awake"
              disabled={!status || busy === "set-keep-awake"} onToggle={() => setKeepAwake(!status?.keep_awake)} />}
          />
        </SetCard>
        {error && <p data-testid="remote-error" className="set-msg set-msg-err" role="alert">{error}</p>}
        {note && <p data-testid="remote-note" className="set-msg set-msg-ok" role="status">{note}</p>}
      </SetGroup>

      {/* Paired phones + pairing a new one */}
      <SetGroup
        title={t("set.uzak.phones")}
        action={devices.length > 0 ? (
          <button type="button" data-testid="remote-remove-all" onClick={removeAll} className="set-link set-gk-act">
            {t("remote.removeAll")}
          </button>
        ) : undefined}
      >
        <SetCard>
          {devices.length === 0 && <SetRow name={t("remote.noDevices")} />}
          {devices.map(device => (
            <div key={device.device_id} data-testid="remote-device" className="set-row set-prov">
              {phoneIc}
              <div className="set-rt">
                <p className="set-name">{stripBidi(device.name) || t("chat.phoneUnnamed")}</p>
                <div className="set-hint">
                  <Lamp tone={device.online ? "ok" : undefined} />
                  {device.online && <>{t("remote.deviceOnline")} · </>}
                  {device.last_seen
                    ? t("remote.deviceLastSeen", { tarih: fmtDate(device.last_seen, lang) })
                    : t("remote.deviceNeverSeen")}
                  {" · "}
                  {t("remote.deviceCreated", { tarih: fmtDate(device.created, lang) })}
                </div>
              </div>
              <button type="button" onClick={() => removeDevice(device)} className="set-link">{t("remote.remove")}</button>
            </div>
          ))}

          {!offer && !pending && (
            <SetRow
              guide="settings-remote-pair"
              name={t("set.uzak.pairNew")}
              hint={expired
                ? <span data-testid="remote-pair-expired">{t("remote.pairExpired")}</span>
                : (enabled ? t("set.uzak.pairHint") : t("remote.err.remoteOff"))}
              control={(
                <button type="button" data-testid="remote-pair" onClick={startPairing}
                  disabled={!enabled || busy === "pair-start"} className="btn btn-ghost btn-sm">
                  {busy === "pair-start" && <Loader2 size={13} className="animate-spin" />}
                  {t("remote.pair")}
                </button>
              )}
            />
          )}
          {offer && !pending && (
            <div className="set-row set-pair" data-guide="settings-remote-pair">
              <div className="set-qr"><QrCode text={offer.qr_url} /></div>
              <div className="set-rt">
                <p className="set-name">{t("set.uzak.pairNew")}</p>
                <div className="set-hint">
                  {t("remote.pairScan")}{" "}
                  <span data-testid="remote-pair-countdown" className="num">
                    {t("remote.pairExpiresIn", { sure: mmss(offer.expires_at - now) })}
                  </span>
                </div>
              </div>
              <button type="button" onClick={cancelPairing} className="btn btn-ghost btn-sm">{t("remote.pairCancel")}</button>
            </div>
          )}
          {pending && (
            <div data-testid="remote-pair-request" className="set-row set-pair-req">
              <div className="set-rt">
                <p className="set-name">
                  {t("remote.pairRequest", { cihaz: stripBidi(pending.device_name) || t("chat.phoneUnnamed") })}
                </p>
                <div className="set-hint">
                  {t("remote.pairCode")}: <b data-testid="remote-sas" className="set-sas">{pending.sas}</b>
                  <br />{t("remote.pairCodeHint")}
                </div>
              </div>
              <span className="set-acts">
                <button type="button" data-testid="remote-approve" onClick={approve} disabled={busy === "pair-approve"}
                  className="btn btn-primary btn-sm">
                  {t("remote.approve")}
                </button>
                <button type="button" data-testid="remote-reject" onClick={reject} disabled={busy === "pair-reject"}
                  className="btn btn-ghost btn-sm btn-danger">
                  {t("remote.reject")}
                </button>
              </span>
            </div>
          )}
        </SetCard>
      </SetGroup>

      {/* Advanced: relay + forget */}
      <details className="set-group set-adv">
        <summary className="set-gk set-adv-sum">{t("set.advanced")} <Chev /></summary>
        <SetCard>
          <SetRow
            name={t("remote.relay")}
            hint={status ? (
              <>
                <p className="set-relay-line">{t("remote.relayCurrent", { url: status.relay_url })}</p>
                <p className="set-relay-line">{t("remote.relayDefault", { url: status.default_relay_url })}</p>
                {needsRepair && <p data-testid="remote-relay-repair" className="set-msg-err">{t("remote.relayNeedsRepair")}</p>}
              </>
            ) : undefined}
            extra={(
              <form className="set-field" onSubmit={e => { e.preventDefault(); if (relayInput.trim()) void saveRelay(relayInput.trim()); }}>
                <input
                  id="remote-relay-input"
                  data-testid="remote-relay-input"
                  value={relayInput}
                  onChange={e => setRelayInput(e.target.value)}
                  placeholder="https://"
                  aria-label={t("remote.relayCustomLabel")}
                  className="set-input set-input-mono"
                />
                <button type="submit" data-testid="remote-relay-save"
                  disabled={!relayInput.trim() || busy === "set-relay-url"} className="btn btn-ghost btn-sm">{t("remote.relaySave")}</button>
                {status?.custom_relay && (
                  <button type="button" onClick={() => saveRelay(null)} disabled={busy === "set-relay-url"}
                    className="set-link">{t("remote.relayUseDefault")}</button>
                )}
              </form>
            )}
          />
          <SetRow
            name={t("remote.forget")}
            hint={t("set.uzak.forgetHint")}
            control={(
              <button type="button" data-testid="remote-forget" onClick={forget} disabled={busy === "forget"}
                className="btn btn-ghost btn-sm btn-danger">
                {t("remote.forgetConfirmButton")}
              </button>
            )}
          />
        </SetCard>
      </details>
    </div>
  );
};
