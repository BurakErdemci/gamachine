/**
 * useVoiceInput — microphone → 16 kHz mono PCM chunks → /transcribe/session → text.
 *
 * DICTATION, not an assistant: the recognised text is handed back through
 * `onText` and the caller drops it at the caret. Nothing is sent on the user's
 * behalf; they press Enter themselves.
 *
 * LIVE (GPU only): the audio goes up as a chunk session (open → 500 ms chunks
 * → finish) so the backend can re-decode what it has so far about once a
 * second and answer with live text. Whether it does is the backend's call: it
 * only has a GPU fast enough for that (~0.5 s per update, measured 28 Sep
 * 2026); on a CPU-only machine every chunk answers with an empty partial and
 * the text appears at stop, after the `transcribing` state. Transport is plain
 * HTTP — the production CSP has no `ws:` entry and it stays that way.
 *
 * Why the renderer downsamples instead of shipping the browser's own
 * MediaRecorder output: the whisper.cpp server is fed 16 kHz mono
 * signed-16-bit PCM wrapped in a WAV by the backend. MediaRecorder produces WebM/Opus on Chromium with no way
 * to ask for raw PCM, so the backend would have to carry a decoder. An
 * AudioWorklet tap plus `renderer/lib/wav.ts` keeps the format contract on one
 * side of the wire.
 *
 * ⚠️ TEARDOWN HAPPENS BEFORE THE REQUEST, not after. The OS microphone
 * indicator is driven by the live MediaStreamTrack; leaving the track open
 * across a network round trip would keep the "recording" light on for as long
 * as transcription takes, which reads to the user as "it is still listening".
 * The tests assert this ordering explicitly.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { apiHataMesaji } from '../../lib/apiError';
import { cevir } from '../../lib/i18n';
import {
  MAX_RECORD_MS,
  WAV_MAX_BYTES,
  WAV_SAMPLE_RATE,
  downsampleTo16k,
  floatTo16BitPcm,
  int16ToBase64,
  wavByteLength,
} from '../../lib/wav';

export type VoiceState = 'idle' | 'recording' | 'transcribing';

export type VoiceErrorKind = 'permission' | 'noDevice' | 'model' | 'engine' | 'empty' | 'server';

export interface VoiceError {
  kind: VoiceErrorKind;
  /** Raw backend detail / exception text — shown in a `title`, never as the label. */
  detail?: string;
}

interface VoiceInputParams {
  /** Backend base URL. Empty until IPC resolves it; the caller disables the button. */
  api: string;
  lang: 'tr' | 'en';
  onText: (text: string) => void;
}

/**
 * Stop every track of a stream we hold no ref to yet.
 *
 * Separate from `teardown()` because the leaks the audit found all happen
 * BEFORE the refs are installed: at that point `teardown()` has nothing to
 * release, so the only handle on the microphone is the local variable.
 */
const releaseStream = (stream: MediaStream | null | undefined) => {
  try { stream?.getTracks?.().forEach(t => t.stop()); } catch { /* no tracks */ }
};

/**
 * Close a context without letting the failure escape.
 *
 * `void ctx.close()` catches only a SYNCHRONOUS throw. `AudioContext.close()`
 * returns a promise, and a browser-level refusal became an unhandled rejection
 * surfacing outside the hook's error handling, after the context ref had
 * already been nulled (audit: `unhandled-cleanup-rejection`). Wrapping the call
 * in `Promise.resolve().then(...)` also covers a stub that throws synchronously
 * or returns a non-promise.
 */
const releaseContext = (ctx: AudioContext | null | undefined) => {
  if (!ctx) return;
  // Handled is not the same as explained: an empty catch left a context that
  // refused to close with no trace at all, and `teardown()` has already dropped
  // the ref, so there is nothing left to retry with either. Warn, never throw.
  Promise.resolve().then(() => ctx.close()).catch((reason) => {
    console.warn('[voice] AudioContext.close rejected:', reason);
  });
};

const WORKLET_URL = '/audio/pcm-capture-worklet.js';
const PROCESSOR_NAME = 'pcm-capture';
/** Elapsed-time refresh. 200 ms is under one mm:ss tick, so the timer never skips a second. */
const TICK_MS = 200;

/**
 * How often the accumulated audio is shipped. The backend decodes at most once
 * a second, so a shorter interval would only add round trips.
 */
const CHUNK_MS = 500;

/**
 * No live text before this much recording. Whisper invents words for the
 * first second of near-silence ("Evet." in 10 of 12 measured clips, 28 Sep
 * 2026). The backend does not decode that early either; this is the second
 * lock, on the side that paints the box.
 */
export const LIVE_TEXT_DELAY_MS = 1500;

/**
 * The final decode. A CPU-only machine with auto language takes ~11.6 s for a
 * 5 s clip, a 60 s recording is two 30 s windows, and the first dictation
 * may also wait for the engine to load — minutes are possible.
 */
const FINISH_TIMEOUT_MS = 360_000;

/** A chunk may carry a live decode (~0.5-1 s on a GPU). */
const CHUNK_TIMEOUT_MS = 20_000;

/** 503 details that mean something other than "the files are missing". */
const engineErrorKind = (detail: unknown): VoiceErrorKind =>
  detail === 'stt_engine_failed' ? 'engine' : 'model';

/**
 * Backend per-chunk ceiling: 65_536 decoded bytes = 32_768 samples.
 *
 * One tick is ~8_000 samples, so this only bites after retries have piled up
 * audio; the remainder stays queued for the next send rather than being sent
 * as a body the backend answers with 413 — a rejection the user cannot act on.
 */
const MAX_CHUNK_SAMPLES = 32_768;

/** Consecutive chunk failures before the recording is abandoned. */
const MAX_CHUNK_FAILURES = 3;

/**
 * The session token, read back from the axios default rather than relied on.
 *
 * `useAuth` puts it on `axios.defaults.headers.common`, but a per-call
 * `headers` object REPLACES the default for that header, so anything that later
 * adds headers here would silently drop auth.
 */
const authHeaders = (): Record<string, string> => {
  const token = (axios as any).defaults?.headers?.common?.['X-Session-Token'];
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['X-Session-Token'] = String(token);
  return headers;
};

export const useVoiceInput = ({ api, lang, onText }: VoiceInputParams) => {
  const [state, setState] = useState<VoiceState>('idle');
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState<VoiceError | null>(null);
  /** The recogniser's running guess. Replaced wholesale on every chunk answer. */
  const [partialText, setPartialText] = useState('');

  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceRef = useRef<AudioNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const rawSampleCountRef = useRef(0);
  const inputRateRef = useRef(WAV_SAMPLE_RATE);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef(0);

  /** Server-side recogniser id. Null means there is nothing to send or finish. */
  const sessionIdRef = useRef<string | null>(null);
  /**
   * Set for exactly the window between `stop()` clearing `sessionIdRef` and
   * its normal finish request settling. `sessionIdRef` being null in that
   * window used to mean `discardSession()` found nothing to clean up if
   * cancel or unmount landed there too — the session could still be alive on
   * the server with nothing left client-side that named it (audit findings,
   * 3 Sep 2026). An extra discard once the finish already succeeded is
   * harmless: the session is gone, the request just answers 404.
   */
  const pendingFinishIdRef = useRef<string | null>(null);
  const chunkTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /**
   * One chunk POST in flight at a time.
   *
   * The backend appends each chunk to one buffer per session, so two
   * overlapping requests would interleave audio in whatever order the network
   * delivered them — i.e. the words would come back scrambled on a slow link. A tick that finds this set simply waits for the next one.
   */
  const sendingRef = useRef(false);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const chunkFailuresRef = useRef(0);

  /**
   * Per-recording cancellation flag, an object rather than a boolean so the
   * in-flight request closes over THIS recording's flag. A shared boolean would
   * be reset by the next `start()` and a late response from the abandoned
   * recording would then be treated as live.
   */
  const runRef = useRef<{ cancelled: boolean }>({ cancelled: true });

  /**
   * SYNCHRONOUS duplicate-start guard.
   *
   * The old guard read React state, which stays `idle` for the whole async
   * acquisition — so two clicks in the same render both passed it, both called
   * `getUserMedia`, and the single set of refs kept only the second. The first
   * microphone track was then unreachable and could never be stopped (audit:
   * `duplicate-start-race`). A ref is written before the first await, so the
   * second call sees it in the same tick.
   */
  const startingRef = useRef(false);

  /**
   * The stream and context DURING acquisition, before the graph is committed.
   *
   * Between `getUserMedia` resolving and `addModule` settling these existed only
   * as `start()` locals, so a `cancel()`/`stop()`/unmount arriving in that window
   * had nothing to release: a worklet fetch that never returns left the
   * microphone on with no way for the user to turn it off (audit round 2,
   * `partial-teardown-on-async-cancel`). `teardown()` drains this too, which
   * makes release possible at any instant rather than only at the await
   * boundaries. The post-await abort checks stay as the second line of defence.
   */
  const pendingRef = useRef<{
    stream: MediaStream | null;
    ctx: AudioContext | null;
    // Present only once the graph is fully built and `start()` is awaiting
    // `/transcribe/session` — the window a cancel used to have no way to
    // reach synchronously, since these two live only as LOCAL variables in
    // `start()` until the atomic final commit near its end (audit finding,
    // 3 Sep 2026, `async-acquisition-graph-leak`: cancelling here stopped the
    // track but left the worklet node and its source connection untouched
    // until — or unless — the pending request eventually settled).
    node?: AudioWorkletNode | null;
    source?: AudioNode | null;
  } | null>(null);

  /**
   * "The thing we are acquiring for is gone." Set by unmount and by `cancel()`,
   * and re-checked after EVERY await inside `start()`: a `getUserMedia` or
   * `addModule` promise that settles after cleanup would otherwise install a
   * live capture graph that no effect will ever tear down again (audit:
   * `async-start-after-unmount`).
   */
  const abortAcquisitionRef = useRef(false);

  // `onText` and `lang` read through refs: `start`/`stop` are handed to a
  // button and to timers, and rebuilding them on every parent render would
  // restart the auto-stop timeout mid-recording.
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const langRef = useRef(lang);
  langRef.current = lang;
  const apiRef = useRef(api);
  apiRef.current = api;

  /** Release every OS-visible resource. Safe to call twice. */
  const teardown = useCallback(() => {
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    if (chunkTimerRef.current) { clearInterval(chunkTimerRef.current); chunkTimerRef.current = null; }
    if (autoStopRef.current) { clearTimeout(autoStopRef.current); autoStopRef.current = null; }
    try { nodeRef.current?.port?.close?.(); } catch { /* already closed */ }
    try { nodeRef.current?.disconnect(); } catch { /* not connected */ }
    nodeRef.current = null;
    try { sourceRef.current?.disconnect(); } catch { /* not connected */ }
    sourceRef.current = null;
    // Tracks first among the things the OS can see — this is what turns the
    // microphone indicator off.
    releaseStream(streamRef.current);
    streamRef.current = null;
    releaseContext(ctxRef.current);
    ctxRef.current = null;
    // Anything an in-flight `start()` has acquired but not yet committed.
    try { pendingRef.current?.node?.port?.close?.(); } catch { /* already closed */ }
    try { pendingRef.current?.node?.disconnect(); } catch { /* not connected */ }
    try { pendingRef.current?.source?.disconnect(); } catch { /* not connected */ }
    releaseStream(pendingRef.current?.stream);
    releaseContext(pendingRef.current?.ctx);
    pendingRef.current = null;
  }, []);

  const clearError = useCallback(() => setError(null), []);

  /**
   * Take up to `maxSamples` from the head of the unsent queue.
   *
   * The queue is drained rather than copied because a chunk that fails has to
   * go back to the FRONT: the recogniser is fed a single ordered stream, so
   * dropping or reordering one chunk corrupts every partial after it.
   */
  const drainQueued = useCallback((maxSamples: number): Float32Array => {
    const queue = chunksRef.current;
    let available = 0;
    for (const c of queue) available += c.length;
    const take = Math.min(available, maxSamples);
    const out = new Float32Array(take);
    let filled = 0;
    while (filled < take && queue.length) {
      const head = queue[0];
      const need = take - filled;
      if (head.length <= need) {
        out.set(head, filled);
        filled += head.length;
        queue.shift();
      } else {
        out.set(head.subarray(0, need), filled);
        queue[0] = head.subarray(need);
        filled += need;
      }
    }
    return out;
  }, []);

  /**
   * Fire-and-forget discard of a specific server-side session id.
   *
   * Split out of `discardSession` so a caller that already knows the id (and
   * has already cleared `sessionIdRef` for its own bookkeeping, as `stop()`'s
   * finish path does) can still release the slot immediately instead of
   * leaving it to the 90 s idle TTL (audit finding, 3 Sep 2026: a normal
   * finish request that failed before reaching the server left the id
   * nowhere for `discardSession` to find, and the slot sat occupied for up
   * to 90 s across a failure the user has already seen an error for).
   */
  const discardSessionId = useCallback((id: string) => {
    // Not awaited on purpose: every caller of this needs to keep going (or is
    // already in a cleanup path) regardless of whether the backend answers.
    // Failing to reach it is harmless either way — the session's own 90 s
    // idle TTL is the backstop. The outer try/catch is for a throw from the
    // CALL ITSELF, not just a rejected promise — this is reachable from
    // `stop()`'s own finish failure, where `axios.post` can fail before it
    // ever returns a promise (measured: an interceptor or transport error at
    // the exact moment a normal finish request already failed the same way).
    try {
      axios.post(
        `${apiRef.current}/transcribe/session/${id}/finish`,
        { discard: true },
        { headers: authHeaders(), timeout: 5000 },
      ).catch((err: any) => {
        // Handled is not the same as explained (same rule as
        // `releaseContext`'s AudioContext.close): an empty catch here left a
        // discard that genuinely could not reach the backend with no trace
        // anywhere, so a caller whose four slots quietly filled up with dead
        // sessions had nothing to read (audit finding, 3 Sep 2026). A 404 is
        // excluded on purpose — it means the session was already gone (the
        // TTL, or a finish that actually succeeded despite its own request
        // failing), which is the discard doing its job, not a failure of it.
        if (err?.response?.status !== 404) {
          console.warn('[voice] session discard failed:', apiHataMesaji(err, err?.message ?? String(err)));
        }
      });
    } catch (err: any) {
      console.warn('[voice] session discard failed:', err?.message ?? String(err));
    }
  }, []);

  const discardSession = useCallback(() => {
    const id = sessionIdRef.current;
    sessionIdRef.current = null;
    if (id) discardSessionId(id);
  }, [discardSessionId]);

  /** Give up on a recording that cannot reach the backend any more. */
  const abandon = useCallback((detail?: string) => {
    runRef.current.cancelled = true;
    teardown();
    chunksRef.current = [];
    discardSession();
    setPartialText('');
    setError({ kind: 'server', detail });
    setState('idle');
    setElapsedMs(0);
  }, [teardown, discardSession]);

  /**
   * One scheduler tick: ship whatever has accumulated, show the new partial.
   *
   * A failure does NOT end the recording — a single dropped request while the
   * user is mid-sentence would throw away audio they cannot re-speak. The
   * samples go back on the queue and ride along with the next tick; only three
   * consecutive failures mean the backend is really gone.
   */
  const sendChunk = useCallback((): Promise<void> => {
    const run = runRef.current;
    const id = sessionIdRef.current;
    // A tick that lands on an in-flight send returns THAT send's promise, so
    // `stop()` can wait the ordering out instead of polling a timer it would
    // have to advance itself.
    if (!id || run.cancelled) return Promise.resolve();
    if (sendingRef.current) return inFlightRef.current ?? Promise.resolve();
    const ratio = inputRateRef.current / WAV_SAMPLE_RATE;
    const samples = drainQueued(Math.max(1, Math.floor(MAX_CHUNK_SAMPLES * ratio)));
    if (samples.length === 0) return Promise.resolve();
    sendingRef.current = true;
    const inFlight = (async () => {
      try {
        const pcm = int16ToBase64(floatTo16BitPcm(downsampleTo16k(samples, inputRateRef.current)));
        const res = await axios.post(
          `${apiRef.current}/transcribe/session/${id}`,
          { pcm_base64: pcm },
          { headers: authHeaders(), timeout: CHUNK_TIMEOUT_MS },
        );
        if (run.cancelled) return;
        chunkFailuresRef.current = 0;
        const partial = typeof res?.data?.partial === 'string' ? res.data.partial : '';
        if (Date.now() - startedAtRef.current >= LIVE_TEXT_DELAY_MS) setPartialText(partial);
      } catch (err: any) {
        if (run.cancelled) return;
        // The route's OWN contract for this status: the session survives the
        // refusal SPECIFICALLY so the caller can still finish what it already
        // accepted. Treating it like any other failure used to retry this
        // audio (which the backend will refuse forever — the budget does not
        // shrink) until the retry count reached MAX_CHUNK_FAILURES and
        // `abandon()` discarded the whole recording, accepted prefix included
        // (audit finding, 3 Sep 2026). The chunk — and the rest of the queue,
        // since none of it will fit either — is dropped rather than requeued.
        if (err?.response?.status === 413 && err?.response?.data?.detail === 'stt_too_large') {
          chunksRef.current = [];
          // `chunkTimerRef` is only armed during live recording: a 413 hit
          // from `stop()`'s OWN drain loop must not call `stop()` again —
          // emptying the queue is enough, the drain loop's own while
          // condition then ends it and proceeds straight to a normal finish.
          if (chunkTimerRef.current !== null) void stopRef.current();
          return;
        }
        chunksRef.current.unshift(samples);
        chunkFailuresRef.current += 1;
        if (chunkFailuresRef.current >= MAX_CHUNK_FAILURES) {
          abandon(apiHataMesaji(err, err?.message ?? String(err)) || undefined);
        }
      } finally {
        // Only THIS run's own settle may clear the shared guard. A run that was
        // cancelled and replaced still owns its promise until it settles; if its
        // `finally` cleared the guard unconditionally, its late settle could
        // free a NEW run's guard while that run's own send was still in flight,
        // letting the scheduler fire a second overlapping request for it (audit
        // finding, 3 Sep 2026 — `runRef.current` had already moved on to the
        // new run's object by the time the stale settle landed).
        if (runRef.current === run) sendingRef.current = false;
      }
    })();
    inFlightRef.current = inFlight;
    return inFlight;
  }, [drainQueued, abandon]);

  const sendChunkRef = useRef(sendChunk);
  sendChunkRef.current = sendChunk;

  const start = useCallback(async () => {
    // Ref first, state second: only the ref is reliable inside the same tick.
    if (startingRef.current || streamRef.current) return;
    if (state !== 'idle') return;
    startingRef.current = true;
    abortAcquisitionRef.current = false;

    // Held locally until the graph is fully built. Every early return below
    // releases these directly, because until the refs are assigned `teardown()`
    // cannot see them.
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;

    try {
      setError(null);

      const media = (globalThis as any).navigator?.mediaDevices;
      if (!media?.getUserMedia) {
        setError({ kind: 'noDevice' });
        return;
      }

      try {
        stream = await media.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      } catch (err: any) {
        // A cancel that landed while this promise was pending has nothing to
        // release yet (no stream was ever acquired), but must not read a
        // deliberately abandoned request as a device/permission failure
        // (audit finding, 3 Sep 2026, `canceled-acquisition-error`).
        if (abortAcquisitionRef.current) return;
        const name = err?.name;
        // NotAllowedError/SecurityError are a REFUSAL (user or policy);
        // NotFound/Overconstrained mean there is no device that fits. Different
        // sentences, because the fix is different: grant permission vs plug
        // something in.
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          setError({ kind: 'permission', detail: err?.message });
        } else {
          setError({ kind: 'noDevice', detail: err?.message ?? String(err) });
        }
        return;
      }

      // Publish immediately: from here on the microphone is live, so anyone
      // calling cancel/stop/unmount must be able to reach it.
      pendingRef.current = { stream, ctx: null };

      // The permission dialog can outlive the component. Bail without touching
      // state — a `setError` here would be a write into an unmounted tree, and
      // the point is to release the microphone, not to report anything.
      if (abortAcquisitionRef.current) { releaseStream(stream); pendingRef.current = null; return; }

      try {
        // Asking for 16000 directly avoids resampling entirely where the browser
        // honours it. Where it refuses (Firefox historically throws), we take the
        // default rate and decimate — hence reading `ctx.sampleRate` below rather
        // than assuming the requested value was applied.
        ctx = new (globalThis as any).AudioContext({ sampleRate: WAV_SAMPLE_RATE });
      } catch {
        try {
          ctx = new (globalThis as any).AudioContext();
        } catch (err: any) {
          releaseStream(stream);
          pendingRef.current = null;
          setError({ kind: 'noDevice', detail: err?.message ?? String(err) });
          return;
        }
      }
      pendingRef.current = { stream, ctx };

      // ONE protected block for every remaining graph step. Each of these could
      // throw after the microphone was already live — the audit reproduced a
      // leak from the `AudioWorkletNode` constructor, from
      // `createMediaStreamSource()` and from `connect()`, all of which used to
      // run outside any cleanup (`partial-teardown-on-exception`).
      // Nullable and initialized, not `let node: AudioWorkletNode;`: a `let`
      // left unassigned is in the temporal dead zone, and even `node?.foo` in
      // the catch below would throw a ReferenceError for an exception that
      // hit before `node` was ever constructed. Matches `stream`/`ctx` above.
      let node: AudioWorkletNode | null = null;
      let source: AudioNode | null = null;
      try {
        await ctx!.audioWorklet.addModule(WORKLET_URL);
        if (abortAcquisitionRef.current) {
          releaseStream(stream); releaseContext(ctx); pendingRef.current = null; return;
        }

        node = new (globalThis as any).AudioWorkletNode(ctx, PROCESSOR_NAME);
        source = ctx!.createMediaStreamSource(stream!);
        source.connect(node);
        // Published as soon as they exist, same reason `stream`/`ctx` were
        // above: `teardown()` (which cancel and unmount both already call)
        // can now release them immediately if a cancellation lands during
        // the session-open await just below, instead of only whenever — or
        // if — that request eventually settles.
        pendingRef.current = { stream, ctx, node, source };
      } catch (err: any) {
        // `node`/`source` may be partially built (constructed but not yet
        // connected, or not constructed at all) — release whichever of them
        // exist. Verification round, 3 Sep 2026: a `connect()` throw left a
        // constructed node and source with no cleanup call on either,
        // because this catch released only `stream`/`ctx`.
        try { node?.port?.close?.(); } catch { /* already closed */ }
        try { node?.disconnect(); } catch { /* not connected */ }
        try { source?.disconnect(); } catch { /* not connected */ }
        releaseStream(stream);
        releaseContext(ctx);
        pendingRef.current = null;
        // See the `getUserMedia` catch above: a cancel already marked this
        // run abandoned, so a late rejection here must not overwrite the
        // clean canceled state with a false device error (audit finding,
        // 3 Sep 2026, `canceled-acquisition-error`). Resources are still
        // released either way — only reporting the error is conditional.
        if (!abortAcquisitionRef.current) {
          setError({ kind: 'noDevice', detail: err?.message ?? String(err) });
        }
        return;
      }

      // Everything the graph holds, for the failure paths below. `teardown()`
      // still cannot see the node and the source (they are locals until the
      // commit), so they are released by hand exactly like the stream is.
      const releaseGraph = () => {
        try { (node as any)?.port?.close?.(); } catch { /* already closed */ }
        try { node?.disconnect(); } catch { /* not connected */ }
        try { source?.disconnect(); } catch { /* not connected */ }
        releaseStream(stream);
        releaseContext(ctx);
        pendingRef.current = null;
      };

      // The session is opened with the microphone already live but BEFORE the
      // recording state is announced: the backend checks the engine files here
      // (and starts the engine without waiting for it), so a missing install is
      // reported as a failure to start rather than as a recording that
      // silently produces nothing.
      let sessionId: string;
      try {
        const res = await axios.post(
          `${apiRef.current}/transcribe/session`,
          { lang: langRef.current },
          { headers: authHeaders(), timeout: 15000 },
        );
        const id = res?.data?.session_id;
        if (typeof id !== 'string' || id === '') throw new Error('stt_no_session_id');
        sessionId = id;
      } catch (err: any) {
        releaseGraph();
        // A cancel already tore this run down and put the hook back at
        // `idle`; a late failure of the very request that raced it must not
        // write a false server error over that clean state (audit finding,
        // 3 Sep 2026, `canceled-acquisition-error`). The graph is still
        // released above regardless — only reporting the error is skipped.
        if (abortAcquisitionRef.current) return;
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        // 503 is normally "the recognition files are missing", but the same
        // status also means "all four recogniser slots are busy" — a temporary
        // condition with a different fix, so it must not read as a broken
        // install.
        if (status === 503 && detail !== 'stt_busy') {
          setError({ kind: engineErrorKind(detail), detail: apiHataMesaji(err, '') || undefined });
        } else if (!err?.response) {
          setError({ kind: 'server', detail: err?.message ?? String(err) });
        } else {
          setError({ kind: 'server', detail: apiHataMesaji(err, `HTTP ${status}`) });
        }
        return;
      }

      // Same window as every other await in here: a cancel/unmount that landed
      // while the session was being opened must not leave a live graph behind,
      // and the session it asked for has to be handed back.
      if (abortAcquisitionRef.current) {
        releaseGraph();
        sessionIdRef.current = sessionId;
        discardSession();
        return;
      }

      // Past this line nothing can throw, so installing the refs here means the
      // refs and the live resources are always in the same state.
      const run = { cancelled: false };
      runRef.current = run;
      sessionIdRef.current = sessionId;
      chunkFailuresRef.current = 0;
      sendingRef.current = false;
      // A run that never sends its OWN chunk before stopping (nothing was
      // queued yet) would otherwise inherit whatever the PREVIOUS run's last
      // send left here. `stop()` awaits `inFlightRef.current` before its
      // drain loop, so a stale unresolved promise from a cancelled prior run
      // blocked this run's finish indefinitely until that old promise
      // happened to settle (verification round, 3 Sep 2026,
      // `cross-run-send-guard`).
      inFlightRef.current = null;
      setPartialText('');
      chunksRef.current = [];
      rawSampleCountRef.current = 0;
      inputRateRef.current = ctx!.sampleRate || WAV_SAMPLE_RATE;
      streamRef.current = stream;
      ctxRef.current = ctx;
      nodeRef.current = node;
      sourceRef.current = source;
      // The refs own these now.
      pendingRef.current = null;


      startedAtRef.current = Date.now();
      setElapsedMs(0);
      setState('recording');
      tickRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startedAtRef.current);
      }, TICK_MS);
      // The scheduler holds the CURRENT `sendChunk` through a ref for the same
      // reason the auto-stop holds the current `stop`: a stale closure would
      // keep sending against state that has moved on.
      chunkTimerRef.current = setInterval(() => { void sendChunkRef.current(); }, CHUNK_MS);
      // Hard ceiling. Without it a forgotten recording runs until the byte budget
      // trips, and the byte budget depends on the sample rate — i.e. the user
      // would get a different maximum length on different machines.
      autoStopRef.current = setTimeout(() => { stopRef.current(); }, MAX_RECORD_MS);

      // Handler installed LAST, after the recording state and the timers are
      // committed. A real MessagePort delivers asynchronously so a message
      // cannot interleave here, but the budget callback calls `stop()`, and with
      // the handler installed first that stop could be immediately overwritten
      // by this function's own `setState('recording')` - leaving the indicator
      // lit over a graph already torn down. The ordering costs nothing and
      // removes the possibility (audit round 2, `premature-stop-state-race`).
      node.port.onmessage = (event: MessageEvent) => {
        const block = event.data as Float32Array;
        if (!block || !block.length) return;
        chunksRef.current.push(block);
        rawSampleCountRef.current += block.length;
        // Budget check in the 16 kHz domain, because that is what actually goes
        // on the wire. Stopping early is better than posting a body the backend
        // answers with 413 — a rejection the user cannot act on.
        const ratio = inputRateRef.current / WAV_SAMPLE_RATE;
        const projected = wavByteLength(Math.floor(rawSampleCountRef.current / ratio));
        if (projected >= WAV_MAX_BYTES) stopRef.current();
      };
    } finally {
      startingRef.current = false;
    }
  }, [state, discardSession]);

  const stop = useCallback(async () => {
    // A stop asked for while `start()` is still awaiting used to fall through
    // this guard (state idle, refs empty) and the acquisition then committed
    // `recording` anyway - the user's stop was silently ignored and the
    // microphone stayed live (audit round 2, `stop-during-async-acquisition`).
    // `!streamRef.current` narrows this to the ACQUIRING window specifically:
    // `startingRef` is still true at the moment the message handler is
    // installed, by which point the graph is committed and a stop there is a
    // real stop that must transcribe, not an abort.
    if (startingRef.current && !streamRef.current) {
      abortAcquisitionRef.current = true;
      teardown();  // drains `pendingRef`, i.e. whatever has been acquired so far
      discardSession();
      setPartialText('');
      setState('idle');
      setElapsedMs(0);
      return;
    }
    if (state !== 'recording' && !streamRef.current) return;
    const run = runRef.current;
    const chunks = chunksRef.current;

    // Order matters — see the header note. Everything the OS can see is gone
    // before a single byte is sent.
    teardown();
    // The unsent tail stays queued: it is exactly the audio the recogniser has
    // not heard yet, and `teardown()` only released OS resources. Clearing it
    // here would drop the last half second — usually the end of a word.
    chunksRef.current = chunks;
    if (run.cancelled) { chunksRef.current = []; discardSession(); setState('idle'); return; }

    setState('transcribing');

    const id = sessionIdRef.current;
    if (!id) {
      // No session means `start()` never got one; there is nothing to finish.
      chunksRef.current = [];
      setState('idle');
      setElapsedMs(0);
      return;
    }

    // Wait out whatever the scheduler still has in flight, then ship the tail.
    // Ordered, one at a time: the backend feeds one recogniser per session.
    //
    // The loop used to give up after the FIRST failed drain send (`=== 0`),
    // fall through, and still call finish() — silently delivering a truncated
    // transcript as if the recording had completed cleanly (audit finding,
    // 3 Sep 2026). `sendChunk`'s own catch already re-queues the failed
    // samples at the FRONT and only calls `abandon()` at the same
    // MAX_CHUNK_FAILURES threshold used during live recording, which also
    // clears `chunksRef` and cancels `run` — so retrying up to that threshold
    // here either delivers every queued sample before finishing, or lets
    // `abandon()` take over and the `run.cancelled` / empty-queue check below
    // returns before finish() is ever called.
    await (inFlightRef.current ?? Promise.resolve());
    while (
      chunksRef.current.length > 0 &&
      !run.cancelled &&
      sessionIdRef.current === id &&
      chunkFailuresRef.current < MAX_CHUNK_FAILURES
    ) {
      await sendChunk();
    }
    chunksRef.current = [];
    if (run.cancelled || sessionIdRef.current !== id) return;
    sessionIdRef.current = null;
    pendingFinishIdRef.current = id;

    try {
      const res = await axios.post(
        `${apiRef.current}/transcribe/session/${id}/finish`,
        {},
        { headers: authHeaders(), timeout: FINISH_TIMEOUT_MS },
      );
      if (run.cancelled) return;
      const text = typeof res?.data?.text === 'string' ? res.data.text : '';
      if (text.trim() === '') {
        setError({ kind: 'empty' });
      } else {
        onTextRef.current(text.trim());
      }
    } catch (err: any) {
      if (run.cancelled) return;
      // The id was already cleared above for this hook's own bookkeeping, so
      // a failure here would otherwise leave nothing for `discardSession` to
      // find. The server may or may not have actually finished the session
      // before the failure — either way, telling it to discard is safe: a
      // session that already finished itself just answers 404, and one still
      // alive frees its slot now instead of at the 90 s idle TTL.
      discardSessionId(id);
      const status = err?.response?.status;
      if (status === 503) {
        setError({ kind: engineErrorKind(err?.response?.data?.detail), detail: apiHataMesaji(err, '') || undefined });
      } else if (!err?.response) {
        // No response at all: the backend is down, or the request never left.
        setError({ kind: 'server', detail: err?.message ?? String(err) });
      } else {
        // 400 / 413 / 401 / 404 / other 5xx all land here. The user-facing
        // sentence is the same "could not reach the backend" one by contract;
        // the raw detail goes into the title so a report can name it.
        setError({ kind: 'server', detail: apiHataMesaji(err, `HTTP ${status}`) });
      }
    } finally {
      // Cleared whether the finish succeeded, failed (already discarded
      // above), or `run.cancelled` short-circuited past both branches — from
      // here on THIS id is either gone server-side or no longer this hook's
      // responsibility to discard a second time. Guarded by identity, not
      // cleared unconditionally: a cancelled run's finish can still be
      // settling here after a NEWER run has already installed its OWN id in
      // `pendingFinishIdRef` — clearing it unconditionally wiped that newer
      // run's tracking before cancel/unmount ever got a chance to read it
      // (verification round, 3 Sep 2026, `transcribing-session-orphan`: the
      // exact cross-run shape `sendingRef`'s own fix above already covers).
      if (pendingFinishIdRef.current === id) pendingFinishIdRef.current = null;
      if (!run.cancelled) {
        setPartialText('');
        setState('idle');
        setElapsedMs(0);
      }
    }
  }, [state, teardown, sendChunk, discardSession, discardSessionId]);

  // Timers hold the CURRENT `stop`, not the one that existed when the timer was
  // armed: `stop` closes over `state` and would otherwise fire a stale copy
  // that sees `state === 'idle'` and returns without encoding anything.
  const stopRef = useRef(stop);
  stopRef.current = stop;

  /**
   * Discards whatever server-side session this hook currently owns, in
   * EITHER of the two shapes that can exist: a live one (`sessionIdRef`) or
   * one `stop()`'s own normal finish request has not yet settled for
   * (`pendingFinishIdRef`) — the two are mutually exclusive at any instant,
   * but a caller cleaning up does not need to know which one it is. Shared by
   * `cancel()` and the unmount cleanup, which used to each check only
   * `sessionIdRef` and so found nothing during that pending-finish window
   * (audit findings, 3 Sep 2026, `transcribing-session-orphan`).
   */
  const discardPending = useCallback(() => {
    discardSession();
    if (pendingFinishIdRef.current) {
      discardSessionId(pendingFinishIdRef.current);
      pendingFinishIdRef.current = null;
    }
  }, [discardSession, discardSessionId]);

  const cancel = useCallback(() => {
    runRef.current.cancelled = true;
    abortAcquisitionRef.current = true;
    teardown();
    chunksRef.current = [];
    // Fire-and-forget: the user asked for the recording to go away, so nothing
    // here waits on the network to tell them it did.
    discardPending();
    setPartialText('');
    setState('idle');
    setElapsedMs(0);
  }, [teardown, discardPending]);

  useEffect(() => () => {
    // Unmount: the flags first, so neither a request already in flight nor an
    // acquisition still resolving can write into a composer that is gone.
    runRef.current.cancelled = true;
    abortAcquisitionRef.current = true;
    teardown();
    // The session lives on the SERVER: without this the recogniser it holds
    // would stay allocated until the 90 s idle TTL, and MAX_SESSIONS is 4 —
    // four closed composers would lock everyone out of dictation.
    discardPending();
  }, [teardown, discardPending]);

  return { state, elapsedMs, error, partialText, start, stop, cancel, clearError };
};

/** `mm:ss` for the recording badge. Kept here so the button and its tests agree. */
export const formatElapsed = (ms: number): string => {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  return `${mm}:${ss}`;
};

/** i18n key for an error kind — one mapping, read by the composer. */
export const voiceErrorText = (err: VoiceError): string => cevir(`mic.err.${err.kind}` as any);
