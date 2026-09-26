import { useState, useCallback, useEffect, useRef } from 'react';
import type { UserData } from '../../components/home/types';
import { cevir } from '../../lib/i18n';

export interface SideMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  /** Assistant only: the question it answers ("Add to main chat" quotes both). */
  question?: string;
  /** Assistant only: the stream reached `done`/`response`. */
  finished?: boolean;
  failed?: boolean;
}

export interface SideAskOptions {
  /** The main chat's answer as it stands on screen, sent only while it streams. */
  liveContext?: string;
  lang?: string;
  thinkingLevel?: string;
}

/**
 * The read-only side question panel over one main chat.
 *
 * Deliberately NOT part of useChat's runtimes: an entry there shows as running
 * or awaiting in the sidebar and raises desktop notifications, and the side
 * chat must leave the main chat's state untouched. Its own stream, Stop and
 * lifetime live here; closing the panel deletes the side chat server-side.
 */
export const useSideChat = (API: string, user: UserData | null) => {
  const [mainId, setMainId] = useState<number | null>(null);
  const [sideId, setSideId] = useState<number | null>(null);
  const [messages, setMessages] = useState<SideMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const sideIdRef = useRef<number | null>(null);
  const mainIdRef = useRef<number | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  // Bumped by every open and close: a stream or request that started before
  // one no longer writes into the panel.
  const epochRef = useRef(0);
  const idRef = useRef(0);
  const nextId = () => Date.now() * 1000 + (idRef.current++ % 1000);

  // Read through refs so the callbacks keep one identity: the unmount
  // cleanup below depends on them, and a new identity would run it (and
  // delete the open side chat) on an ordinary re-render.
  const apiRef = useRef(API);
  apiRef.current = API;
  const userRef = useRef(user);
  userRef.current = user;
  const headers = useCallback((json = false): Record<string, string> => ({
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    'X-Session-Token': userRef.current?.sessionToken ?? '',
  }), []);

  const createSide = useCallback(async (convId: number): Promise<number | null> => {
    const res = await fetch(`${apiRef.current}/conversations/${convId}/side`, { method: 'POST', headers: headers() });
    if (!res.ok) return null;
    const body = await res.json().catch(() => null);
    return typeof body?.side_id === 'number' ? body.side_id : null;
  }, [headers]);

  const discard = useCallback((id: number | null, keepalive = false) => {
    if (id == null || !apiRef.current) return;
    fetch(`${apiRef.current}/conversations/${id}/side`, { method: 'DELETE', headers: headers(), keepalive }).catch(() => {});
  }, [headers]);

  const close = useCallback(() => {
    epochRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    discard(sideIdRef.current);
    sideIdRef.current = null;
    mainIdRef.current = null;
    setSideId(null);
    setMainId(null);
    setMessages([]);
    setLoading(false);
  }, [discard]);

  const open = useCallback(async (convId: number): Promise<boolean> => {
    if (!apiRef.current || !userRef.current) return false;
    if (mainIdRef.current === convId && sideIdRef.current != null) return true;
    if (mainIdRef.current != null) close();
    const epoch = ++epochRef.current;
    mainIdRef.current = convId;
    setMainId(convId);
    setMessages([]);
    let id: number | null = null;
    try { id = await createSide(convId); } catch { id = null; }
    if (epochRef.current !== epoch) {
      // Closed (or reopened elsewhere) while the request was in flight.
      discard(id);
      return false;
    }
    if (id == null) {
      mainIdRef.current = null;
      setMainId(null);
      return false;
    }
    sideIdRef.current = id;
    setSideId(id);
    return true;
  }, [close, createSide, discard]);

  const stop = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    const id = sideIdRef.current;
    if (id != null && apiRef.current) {
      fetch(`${apiRef.current}/chat-stop/${id}`, { method: 'POST', headers: headers() }).catch(() => {});
    }
    setLoading(false);
  }, [headers]);

  const ask = useCallback(async (question: string, opts: SideAskOptions = {}) => {
    const q = question.trim();
    if (!q || !apiRef.current || !userRef.current || sideIdRef.current == null || controllerRef.current) return;
    const epoch = epochRef.current;
    const answerId = nextId();
    setMessages(prev => [
      ...prev,
      { id: nextId(), role: 'user', content: q },
      { id: answerId, role: 'assistant', content: '', question: q },
    ]);
    const patch = (fn: (m: SideMessage) => SideMessage) => {
      if (epochRef.current !== epoch) return;
      setMessages(prev => prev.map(m => (m.id === answerId ? fn(m) : m)));
    };
    const fail = (text: string) => patch(m => ({
      ...m, failed: true, content: m.content + (m.content ? '\n\n' : '') + `❌ ${text}`,
    }));
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);

    const post = (id: number) => fetch(`${apiRef.current}/conversations/${id}/side-stream`, {
      method: 'POST',
      signal: controller.signal,
      headers: headers(true),
      body: JSON.stringify({
        message: q,
        live_context: opts.liveContext || '',
        language: opts.lang || 'tr',
        thinking_level: opts.thinkingLevel && ['off', 'low', 'medium', 'high'].includes(opts.thinkingLevel)
          ? opts.thinkingLevel : 'medium',
        effort_level: opts.thinkingLevel || 'medium',
      }),
    });

    let finished = false;
    try {
      let target = sideIdRef.current as number;
      let res = await post(target);
      if (res.status === 404 && mainIdRef.current != null) {
        // The idle sweep took the side chat; a fresh one carries on.
        const fresh = await createSide(mainIdRef.current);
        if (fresh != null && epochRef.current === epoch) {
          sideIdRef.current = fresh;
          setSideId(fresh);
          target = fresh;
          res = await post(target);
        }
      }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = typeof body?.detail === 'string' ? body.detail : cevir('side.failed');
        fail(detail);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) { fail(cevir('side.failed')); return; }
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (epochRef.current === epoch) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          if (!frame.startsWith('data: ')) continue;
          let data: any;
          try { data = JSON.parse(frame.slice(6)); } catch { continue; }
          if (data.conversation_id != null && Number(data.conversation_id) !== target) continue;
          if (data.type === 'text' && data.content) {
            patch(m => ({ ...m, content: m.content + data.content }));
          } else if (data.type === 'response') {
            finished = true;
            patch(m => ({ ...m, content: data.content || m.content, finished: true }));
          } else if (data.type === 'done') {
            finished = true;
            const note = data.stop_message ? String(data.stop_message) : '';
            patch(m => ({ ...m, finished: true, content: note && !m.content ? note : m.content }));
          } else if (data.type === 'error' && data.message) {
            fail(String(data.message));
          }
        }
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError' && !finished) fail(cevir('side.failed'));
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (epochRef.current === epoch) {
        setLoading(false);
        patch(m => ({ ...m, finished: true }));
      }
    }
  }, [headers, createSide]);

  // Best effort: the server's idle sweep catches whatever these miss.
  useEffect(() => {
    const onUnload = () => discard(sideIdRef.current, true);
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, [discard]);
  useEffect(() => () => {
    controllerRef.current?.abort();
    discard(sideIdRef.current, true);
  }, [discard]);

  return { isOpen: mainId != null, mainId, sideId, messages, loading, open, close, ask, stop };
};

/** `> Yan soru: …` / `> Cevap: …` quote for the main chat's message box. */
export const sideQuote = (question: string, answer: string, labels = {
  question: cevir('side.quoteQuestion'), answer: cevir('side.quoteAnswer'),
}): string => {
  const quote = (label: string, text: string) => text.trim().split('\n')
    .map((line, i) => `> ${i === 0 ? `${label}: ` : ''}${line}`).join('\n');
  return `${quote(labels.question, question)}\n${quote(labels.answer, answer)}`;
};
