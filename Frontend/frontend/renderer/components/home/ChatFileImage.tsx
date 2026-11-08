import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import axios from "axios";

import { linkTuru, yerelYolaCevir } from "../../lib/chatLink";
import { useLang } from "../../lib/i18n";

/**
 * An image in a message. Only `gamachine-file:<id>` loads: the id names a copy the
 * `show_image` tool stored for this chat, fetched through the backend as a blob (the
 * CSP allows `img-src blob:` and nothing remote). A local path or an http(s) address is
 * never loaded: it shows a chip saying the image was not shown through the image tool.
 */

const FILE_SRC = /^gamachine-file:([A-Za-z0-9-]{8,64})$/;
/** One reply frame of the browser remote carries ~520 KB of image; the route re-encodes to fit. */
const REMOTE_MAX_BYTES = 480000;
const REMOTE_BASE = "remote://backend";

type Loaded = { url: string } | { reason: "notFound" | "failed" };
type Slot = { refs: number; promise: Promise<Loaded> };

// One fetch per id however many bubbles show it.
const slots = new Map<string, Slot>();
// Loaded images outlive the bubble that asked for them: the chat re-renders (and remounts its
// markdown) on every stream frame, and a URL dropped with the bubble meant a new fetch and an
// empty box on every remount, so the image never settled and the thread kept jumping (4 Nov 2026).
// The newest READY_MAX stay; an older one is revoked only once no bubble uses it.
const READY_MAX = 64;
const ready = new Map<string, string>();

/** Tests only: start from an empty image cache. */
export function resetChatImagesForTests() {
  slots.clear();
  ready.clear();
}

function keepReady(id: string, url: string) {
  ready.delete(id);
  ready.set(id, url);
  while (ready.size > READY_MAX) {
    const [oldId, oldUrl] = ready.entries().next().value as [string, string];
    ready.delete(oldId);
    if (!slots.has(oldId)) URL.revokeObjectURL(oldUrl);
  }
}

const backendBase = (): Promise<string> =>
  Promise.resolve((window as any).ipc?.invoke?.("get-backend-base-url"))
    .then((base) => (typeof base === "string" ? base.replace(/\/$/, "") : ""))
    .catch(() => "");

async function load(id: string): Promise<Loaded> {
  try {
    const base = await backendBase();
    const query = base.startsWith(REMOTE_BASE) ? `?max_bytes=${REMOTE_MAX_BYTES}` : "";
    const res = await axios.get(`${base}/chat-files/${id}${query}`, { responseType: "blob" });
    return { url: URL.createObjectURL(res.data as Blob) };
  } catch (err: any) {
    const status = err?.response?.status;
    return { reason: status === 404 || status === 403 ? "notFound" : "failed" };
  }
}

function acquire(id: string): Slot {
  let slot = slots.get(id);
  if (!slot) {
    const cached = ready.get(id);
    const created: Slot = { refs: 0, promise: Promise.resolve({ reason: "failed" }) };
    created.promise = cached ? Promise.resolve({ url: cached }) : load(id).then((result) => {
      // Kept even when every bubble left while the fetch was out: the remounted bubble takes it.
      if ("url" in result) keepReady(id, result.url);
      return result;
    });
    slots.set(id, created);
    slot = created;
  }
  slot.refs += 1;
  return slot;
}

function release(id: string, slot: Slot) {
  slot.refs -= 1;
  if (slot.refs > 0) return;
  // The URL stays in `ready` for the next bubble; keepReady revokes it once evicted and unused.
  if (slots.get(id) === slot) slots.delete(id);
}

const Chip = ({ name, reason }: { name: string; reason: string }) => {
  const { t } = useLang();
  return <span className="chip-file chat-img-chip">{t("chat.image.chip", { name })} · {reason}</span>;
};

/**
 * Every enlarged chat image, the agent's and the user's own. Portalled to <body>: drawn inside
 * the thread, an ancestor became the containing block of `position: fixed`, so the picture was
 * clipped to the thread and the header covered the close button (8 Nov 2026).
 */
export const ImageLightbox = ({ url, label, onClose }: { url: string; label: string; onClose: () => void }) => {
  const { t } = useLang();
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      // preventDefault tells browser full mode (lib/browserFull.ts) the key was used here.
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="chat-img-lightbox" role="dialog" aria-modal="true" aria-label={label}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <button ref={closeRef} type="button" className="chat-img-close" aria-label={t("chat.image.close")} onClick={onClose}>×</button>
      <img src={url} alt={label} />
    </div>,
    document.body,
  );
};

const StoredImage = ({ id, alt }: { id: string; alt: string }) => {
  const { t } = useLang();
  const [state, setState] = useState<Loaded | null>(() => {
    const url = ready.get(id);
    return url ? { url } : null;
  });
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const name = alt || id.slice(0, 8);

  useEffect(() => {
    let live = true;
    const cached = ready.get(id);
    setState(cached ? { url: cached } : null);
    const slot = acquire(id);
    slot.promise.then((result) => { if (live) setState(result); });
    return () => { live = false; release(id, slot); };
  }, [id]);

  const close = () => {
    setOpen(false);
    opener.current?.focus();
  };

  if (state && "reason" in state) {
    return <Chip name={name} reason={t(state.reason === "notFound" ? "chat.image.notFound" : "chat.image.failed")} />;
  }
  const url = state && "url" in state ? state.url : null;
  return (
    <figure className="chat-img">
      {url ? (
        <button ref={opener} type="button" aria-label={t("chat.image.chip", { name })} onClick={() => setOpen(true)}>
          <img src={url} alt={alt} />
        </button>
      ) : (
        <span className="chat-img-ph" role="status" aria-label={t("chat.image.loading")} />
      )}
      {alt && <figcaption>{alt}</figcaption>}
      {open && url && <ImageLightbox url={url} label={name} onClose={close} />}
    </figure>
  );
};

export const ChatFileImage = ({ src, alt }: { src?: string; alt?: string }) => {
  const { t } = useLang();
  const href = (src ?? "").trim();
  const label = (alt ?? "").trim();
  const id = FILE_SRC.exec(href)?.[1];
  if (id) return <StoredImage id={id} alt={label} />;
  let name = label;
  if (/^https?:/i.test(href)) {
    try { name = new URL(href).host; } catch { /* keep the alt text */ }
  } else if (linkTuru(href) === "yerel-dosya") {
    name = yerelYolaCevir(href).split(/[\\/]/).filter(Boolean).pop() || label;
  }
  return <Chip name={name} reason={t("chat.image.useTool")} />;
};
