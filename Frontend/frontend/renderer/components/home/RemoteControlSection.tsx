import { useCallback, useEffect, useRef, useState } from "react";
import { Smartphone, Loader2, Trash2 } from "lucide-react";
import { create as createQr } from "qrcode";

import { useLang } from "../../lib/i18n";
import { stripBidi } from "../../lib/modelText";
import { confirmDialog } from "../ui/ConfirmDialog";
import {
  remoteCall, remoteErrorKey,
  type PairOffer, type PendingPair, type RemoteDevice, type RemoteResult, type RemoteStatus,
} from "../../lib/remoteControl";

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
    <svg data-testid="remote-qr" viewBox={`${-quiet} ${-quiet} ${full} ${full}`} width={200} height={200}
      shapeRendering="crispEdges" className="rounded-lg">
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

const rowClass = "p-3 rounded-xl border border-white/[0.07] bg-white/[0.03]";
const labelClass = "block text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em] mb-1.5";
const smallBtn = "px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-colors disabled:opacity-50";
const neutralBtn = `${smallBtn} bg-white/[0.04] border-white/[0.08] text-slate-300 hover:bg-white/[0.08]`;

interface Props {
  /** Every status this section reads, so the always-visible badge follows at once. */
  onStatus?: (status: RemoteStatus) => void;
  statusPollMs?: number;
  pendingPollMs?: number;
}

export const RemoteControlSection = ({ onStatus, statusPollMs = STATUS_POLL_MS, pendingPollMs = PENDING_POLL_MS }: Props) => {
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
    }
  };

  const saveRelay = async (url: string | null) => {
    const res = await run("set-relay-url", url);
    if (res.ok) {
      setNeedsRepair(!!res.data?.devices_need_repair);
      setNote(t("remote.relaySaved"));
      if (url === null) setRelayInput("");
      void refresh();
    }
  };

  const setKeepAwake = async (on: boolean) => {
    const res = await run("set-keep-awake", on);
    if (res.ok) void refresh();
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
  const dot = !status?.enabled ? "bg-slate-600"
    : status.connected ? "bg-emerald-400"
      : status.gave_up || status.last_error ? "bg-red-500" : "bg-yellow-400 animate-pulse";

  return (
    <div className="space-y-3" data-testid="remote-section">
      {/* On / off */}
      <div className={`flex items-center justify-between gap-3 ${rowClass}`}>
        <div className="flex items-center gap-2.5 min-w-0">
          <Smartphone size={15} className="text-slate-400 shrink-0" />
          <div className="min-w-0">
            <p className="text-xs font-semibold text-slate-200">{t("remote.title")}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 leading-snug">{t("remote.hint")}</p>
            <div className="flex items-center gap-1.5 mt-1">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
              <span data-testid="remote-status" className="text-[10px] text-slate-400">{statusText}</span>
              {status?.enabled && status.connected && status.phones_online > 0 && (
                <span className="text-[10px] text-slate-500">· {t("remote.status.phonesOnline", { sayi: status.phones_online })}</span>
              )}
            </div>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={t("remote.title")}
          data-testid="remote-toggle"
          onClick={toggle}
          disabled={!status || busy === "enable" || busy === "disable"}
          className={`relative w-10 h-5 shrink-0 rounded-full transition-colors duration-200 focus:outline-none disabled:opacity-50 ${
            enabled ? "bg-purple-600" : "bg-slate-700"
          }`}
        >
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${
            enabled ? "translate-x-5" : "translate-x-0"
          }`} />
        </button>
      </div>

      {error && <p data-testid="remote-error" className="text-[11px] text-red-400">{error}</p>}
      {note && <p data-testid="remote-note" className="text-[11px] text-emerald-400">{note}</p>}

      {/* Pairing */}
      <div className={rowClass}>
        {!offer && !pending && (
          <button type="button" data-testid="remote-pair" onClick={startPairing}
            disabled={!enabled || busy === "pair-start"} className={neutralBtn}>
            {busy === "pair-start" && <Loader2 size={11} className="inline mr-1 animate-spin" />}
            {t("remote.pair")}
          </button>
        )}
        {expired && !offer && !pending && (
          <p data-testid="remote-pair-expired" className="text-[10.5px] text-slate-400 mt-2">{t("remote.pairExpired")}</p>
        )}
        {offer && !pending && (
          <div className="flex flex-col items-center gap-2">
            <p className="text-[11px] text-slate-300">{t("remote.pairScan")}</p>
            <QrCode text={offer.qr_url} />
            <p data-testid="remote-pair-countdown" className="text-[10.5px] text-slate-400 tabular-nums">
              {t("remote.pairExpiresIn", { sure: mmss(offer.expires_at - now) })}
            </p>
            <button type="button" onClick={cancelPairing} className={neutralBtn}>{t("remote.pairCancel")}</button>
          </div>
        )}
        {pending && (
          <div data-testid="remote-pair-request" className="flex flex-col items-center gap-2">
            <p className="text-xs font-semibold text-slate-200">
              {t("remote.pairRequest", { cihaz: stripBidi(pending.device_name) || t("chat.phoneUnnamed") })}
            </p>
            <p className="text-[10px] text-slate-500 uppercase tracking-[0.14em]">{t("remote.pairCode")}</p>
            <p data-testid="remote-sas" className="text-3xl font-bold tracking-[0.3em] text-white tabular-nums">{pending.sas}</p>
            <p className="text-[10.5px] text-slate-400">{t("remote.pairCodeHint")}</p>
            <div className="flex gap-2">
              <button type="button" data-testid="remote-approve" onClick={approve} disabled={busy === "pair-approve"}
                className={`${smallBtn} bg-blue-600/20 border-blue-500/40 text-blue-300 hover:bg-blue-600/30`}>
                {t("remote.approve")}
              </button>
              <button type="button" data-testid="remote-reject" onClick={reject} disabled={busy === "pair-reject"}
                className={`${smallBtn} bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20`}>
                {t("remote.reject")}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Paired devices */}
      <div className={rowClass}>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em]">{t("remote.devices")}</span>
          {devices.length > 0 && (
            <button type="button" data-testid="remote-remove-all" onClick={removeAll}
              className="text-[10.5px] text-red-500/70 hover:text-red-400">{t("remote.removeAll")}</button>
          )}
        </div>
        {devices.length === 0 ? (
          <p className="text-[10.5px] text-slate-500">{t("remote.noDevices")}</p>
        ) : (
          <ul className="space-y-1.5">
            {devices.map(device => (
              <li key={device.device_id} data-testid="remote-device" className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs text-slate-200 truncate">
                    {stripBidi(device.name) || t("chat.phoneUnnamed")}
                    {device.online && <span className="ml-1.5 text-[10px] text-emerald-400">{t("remote.deviceOnline")}</span>}
                  </p>
                  <p className="text-[10px] text-slate-500">
                    {t("remote.deviceCreated", { tarih: fmtDate(device.created, lang) })}
                    {" · "}
                    {device.last_seen
                      ? t("remote.deviceLastSeen", { tarih: fmtDate(device.last_seen, lang) })
                      : t("remote.deviceNeverSeen")}
                  </p>
                </div>
                <button type="button" onClick={() => removeDevice(device)}
                  className="flex items-center gap-1 text-[10.5px] text-red-500/70 hover:text-red-400 shrink-0">
                  <Trash2 size={11} /> {t("remote.remove")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Relay */}
      <div className={rowClass}>
        <span className={labelClass}>{t("remote.relay")}</span>
        {status && (
          <>
            <p className="text-[10.5px] text-slate-300 break-all">{t("remote.relayCurrent", { url: status.relay_url })}</p>
            <p className="text-[10px] text-slate-500 break-all">{t("remote.relayDefault", { url: status.default_relay_url })}</p>
          </>
        )}
        <label className="block text-[10.5px] text-slate-400 mt-2 mb-1" htmlFor="remote-relay-input">{t("remote.relayCustomLabel")}</label>
        <div className="flex gap-1.5">
          <input
            id="remote-relay-input"
            data-testid="remote-relay-input"
            value={relayInput}
            onChange={e => setRelayInput(e.target.value)}
            placeholder="https://"
            className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-white text-xs outline-none focus:border-blue-500/60"
          />
          <button type="button" data-testid="remote-relay-save" onClick={() => saveRelay(relayInput.trim())}
            disabled={!relayInput.trim() || busy === "set-relay-url"} className={neutralBtn}>{t("remote.relaySave")}</button>
        </div>
        {status?.custom_relay && (
          <button type="button" onClick={() => saveRelay(null)} disabled={busy === "set-relay-url"}
            className="mt-1.5 text-[10.5px] text-slate-400 hover:text-slate-200">{t("remote.relayUseDefault")}</button>
        )}
        {needsRepair && (
          <p data-testid="remote-relay-repair" className="mt-1.5 text-[10.5px] text-amber-400">{t("remote.relayNeedsRepair")}</p>
        )}
      </div>

      {/* Keep awake */}
      <label className={`flex items-start gap-2.5 ${rowClass} cursor-pointer`}>
        <input
          type="checkbox"
          data-testid="remote-keep-awake"
          checked={!!status?.keep_awake}
          disabled={!status || busy === "set-keep-awake"}
          onChange={e => setKeepAwake(e.target.checked)}
          className="mt-0.5"
        />
        <span className="min-w-0">
          <span className="block text-xs font-semibold text-slate-200">{t("remote.keepAwake")}</span>
          <span className="block text-[10px] text-slate-500 mt-0.5 leading-snug">{t("remote.keepAwakeHint")}</span>
        </span>
      </label>

      {/* Forget */}
      <button type="button" data-testid="remote-forget" onClick={forget} disabled={busy === "forget"}
        className="flex items-center gap-1.5 text-[11px] text-red-500/70 hover:text-red-400 transition-colors disabled:opacity-50">
        <Trash2 size={12} /> {t("remote.forget")}
      </button>
    </div>
  );
};
