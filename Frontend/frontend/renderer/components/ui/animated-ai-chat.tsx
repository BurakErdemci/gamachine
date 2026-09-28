"use client";

import { useEffect, useRef, useCallback, useTransition, useMemo } from "react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import {
    FileUp,
    CircleUserRound,
    ArrowUpIcon,
    Paperclip,
    PlusIcon,
    SendIcon,
    XIcon,
    Square,
    Sparkles,
    Mic,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import * as React from "react"
import { SkillsGallery, CommandMeta } from "../home/SkillsGallery";
import { useLang } from "../../lib/i18n";
import { useVoiceInput, formatElapsed } from "../../hooks/home/useVoiceInput";
import type { Conversation } from "../home/types";
import { mentionQueryAt, mentionTargets, findMentions, mentionLabel } from "../../lib/chatMentions";
import { MessageQueue, type MessageQueueProps, type QueueItemView } from "./message-queue";

interface UseAutoResizeTextareaProps {
    minHeight: number;
    maxHeight?: number;
}

function useAutoResizeTextarea({
    minHeight,
    maxHeight,
}: UseAutoResizeTextareaProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const adjustHeight = useCallback(
        (reset?: boolean) => {
            const textarea = textareaRef.current;
            if (!textarea) return;

            if (reset) {
                textarea.style.height = `${minHeight}px`;
                return;
            }

            textarea.style.height = `${minHeight}px`;
            const newHeight = Math.max(
                minHeight,
                Math.min(
                    textarea.scrollHeight,
                    maxHeight ?? Number.POSITIVE_INFINITY
                )
            );

            textarea.style.height = `${newHeight}px`;
        },
        [minHeight, maxHeight]
    );

    useEffect(() => {
        const textarea = textareaRef.current;
        if (textarea) {
            textarea.style.height = `${minHeight}px`;
        }
    }, [minHeight]);

    useEffect(() => {
        const handleResize = () => adjustHeight();
        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, [adjustHeight]);

    return { textareaRef, adjustHeight };
}

interface CommandSuggestion {
    icon: React.ReactNode;
    label: string;
    description: string;
    prefix: string;
    isSkill?: boolean;  // backend 'skills' listesinde mi (palette'te rozet için)
}

interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  containerClassName?: string;
  showRing?: boolean;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, containerClassName, showRing = true, ...props }, ref) => {
    const [isFocused, setIsFocused] = React.useState(false);
    
    return (
      <div className={cn(
        "relative",
        containerClassName
      )}>
        <textarea
          className={cn(
            "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
            "transition-all duration-200 ease-in-out",
            "placeholder:text-muted-foreground",
            "disabled:cursor-not-allowed disabled:opacity-50",
            showRing ? "focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0" : "",
            className
          )}
          ref={ref}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          {...props}
        />
        
        {showRing && isFocused && (
          <motion.span 
            className="absolute inset-0 rounded-md pointer-events-none ring-2 ring-offset-0 ring-violet-500/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
        )}

        {props.onChange && (
          <div 
            className="absolute bottom-2 right-2 opacity-0 w-2 h-2 bg-violet-500 rounded-full"
            style={{
              animation: 'none',
            }}
            id="textarea-ripple"
          />
        )}
      </div>
    )
  }
)
Textarea.displayName = "Textarea"

export function AnimatedChatInput({
    value,
    setValue,
    onSendMessage,
    onCommand,
    onStop,
    onFileDrop,
    isLoading,
    // Varsayılanlar yalnız SON ÇARE: çağrı yerleri metni i18n'den geçiriyor.
    // Eskiden burada "Ask zap a question..." yazıyordu — başka bir ürünün
    // şablonundan kalmış bir metin, ve kullanıcının ilk yazacağı yerde duruyordu.
    placeholder = "Type a message...",
    className,
    disabled = false,
    disabledPlaceholder = "Unavailable",
    slashCommands = [],
    skills = [],
    commandMeta = [],
    galleryProvider = 'claude',
    // Backend base URL. Empty until `useAppInitialization` resolves it; the mic
    // button stays disabled while it is.
    api = '',
    chats = [],
    currentChatId = null,
    queue,
}: {
    value: string;
    setValue: (val: string) => void;
    onSendMessage: (val: string, images?: string[], videos?: any[]) => void;
    onCommand?: (cmd: string) => boolean;
    onStop?: () => void;
    onFileDrop?: (entry: { path: string, name: string }) => void;
    isLoading: boolean;
    placeholder?: string;
    className?: string;
    disabled?: boolean;
    disabledPlaceholder?: string;
    slashCommands?: string[];  // backend'den gelen Claude Code slash komutları (isimler, '/'siz)
    skills?: string[];         // slash komutlarının skill olan alt kümesi (rozet için)
    commandMeta?: CommandMeta[];  // {name, description, argumentHint, insert?} — Skills galerisi için
    galleryProvider?: string;     // 'claude' | 'codex' | 'agy' — galeri gösterim/insert davranışı
    api?: string;
    chats?: Conversation[];       // the `@` menu's targets (the user's chats and branches)
    currentChatId?: number | null;
    // Messages sent while this chat's turn runs; drawn above the text box.
    queue?: MessageQueueProps;
}) {
    // Typing state is INTERNAL — does not propagate to parent on every keystroke.
    const [internalValue, setInternalValue] = useState(value);
    const [attachments, setAttachments] = useState<{ name: string, data: string, type: 'image' | 'file' | 'video', path?: string, url?: string }[]>([]);
    const { t, lang } = useLang();
    const [activeSuggestion, setActiveSuggestion] = useState<number>(-1);
    const [showCommandPalette, setShowCommandPalette] = useState(false);
    const { textareaRef, adjustHeight } = useAutoResizeTextarea({
        minHeight: 60,
        maxHeight: 200,
    });
    const [inputFocused, setInputFocused] = useState(false);
    const [showSkillsGallery, setShowSkillsGallery] = useState(false);
    const commandPaletteRef = useRef<HTMLDivElement>(null);
    const galleryRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // setTimeout(0) callbacks scheduled below (caret/scroll work that has to
    // run after React commits the value) had no cleanup, so unmounting the
    // composer mid-dictation left them scheduled; they later ran against a
    // detached textarea ref (audit finding, 3 Sep 2026 — harmless in effect,
    // since every access below is null-guarded, but not free to leave
    // scheduled). `scheduleDeferred` tracks each one so unmount can cancel it.
    // Declared HERE, ahead of every caller: two of them (`handleSelectCommand`,
    // `insertAtCaret`) used to schedule with a raw `setTimeout` that bypassed
    // this Set entirely (verification round, 3 Sep 2026) — `insertAtCaret`'s
    // own `useCallback` dependency array needs `scheduleDeferred` to already
    // exist at the point THAT line runs, which is why this cannot sit further
    // down the way the two dictation-only callers below do it (their reference
    // is only inside a closure body, evaluated later, not in a deps array
    // evaluated immediately at render time).
    const pendingTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
    const scheduleDeferred = useCallback((fn: () => void) => {
        const id = setTimeout(() => { pendingTimersRef.current.delete(id); fn(); }, 0);
        pendingTimersRef.current.add(id);
    }, []);
    useEffect(() => () => {
        pendingTimersRef.current.forEach((id) => clearTimeout(id));
        pendingTimersRef.current.clear();
    }, []);

    // Galeriden seçim: hazır metin (Claude: '/<isim> '; Codex: skill defaultPrompt'u)
    // girdiye yazılır, kullanıcı (gerekirse düzenleyip) Enter'a basar — yıkıcı komutların
    // kazara tetiklenmemesi için otomatik GÖNDERİLMEZ.
    const handleSelectCommand = (insertText: string) => {
        setInternalValue(insertText);
        setValue(insertText);
        setShowSkillsGallery(false);
        setShowCommandPalette(false);
        // `scheduleDeferred`, not a raw `setTimeout`: this one bypassed the
        // unmount-cleared timer Set (verification round, 3 Sep 2026,
        // `uncancelled-composer-callback`) and could still run its callback
        // against a detached textarea ref after the composer unmounted.
        // Referencing it here is safe though it is declared further down in
        // this component — the callback only ever runs later, by which point
        // the render that defines it has already completed.
        scheduleDeferred(() => { textareaRef.current?.focus(); adjustHeight(); });
    };

    // ——— Voice dictation ———
    //
    // The microphone is driven from here, NOT from ChatPanel: what is being
    // typed lives in `internalValue`, and text pushed in from outside through
    // `setValue` would overwrite the textarea (see the sync effect below).
    // Inserting at the caret is the job of the only place that sees the text —
    // `handleSelectCommand` is here for the same reason.
    const insertAtCaret = useCallback((text: string) => {
        const ta = textareaRef.current;
        const base = ta ? ta.value : internalValue;
        const start = ta && ta.selectionStart != null ? ta.selectionStart : base.length;
        const end = ta && ta.selectionEnd != null ? ta.selectionEnd : start;
        const before = base.slice(0, start);
        const after = base.slice(end);
        // Separate with a single space — but add no separator at the start of
        // the line or right after existing whitespace: prefixing every dictated
        // piece would start the text with a space when speaking into an empty box.
        const separator = before.length > 0 && !/\s$/.test(before) ? ' ' : '';
        const inserted = separator + text;
        const next = before + inserted + after;
        setInternalValue(next);
        setValue(next);
        const caret = before.length + inserted.length;
        // `scheduleDeferred`, not a raw `setTimeout` (same reason as
        // `handleSelectCommand` above): the caret has to move AFTER React has
        // written the value, otherwise the controlled textarea's re-render
        // throws the selection to the end, but the callback still needs to be
        // cancellable if the composer unmounts first.
        scheduleDeferred(() => {
            const el = textareaRef.current;
            if (el) { el.focus(); el.setSelectionRange(caret, caret); }
            adjustHeight();
        });
    }, [internalValue, setValue, adjustHeight, scheduleDeferred]);

    // ——— Live dictation ———
    //
    // While the microphone is on, the composer owns a RANGE of the text — the
    // interim range — that is rewritten from scratch on every partial result.
    // Rewriting a range rather than appending is what makes the recogniser's
    // corrections visible: vosk revises the words it already emitted, so text
    // that was merely appended would keep the wrong guess on screen forever.
    //
    // `base` is the text WITHOUT anything dictated, i.e. the string the interim
    // is spliced into; `original` is what the box held before recording, kept
    // so a cancel or a failure can put it back exactly.
    const dictationRef = useRef<{ anchor: number; end: number; base: string; original: string } | null>(null);

    // True once a dictation ends by error or cancel and no final has claimed it
    // yet. `handleFinalText` reads this to tell "a final arrived after this
    // dictation was aborted" apart from "no dictation was ever armed" — both
    // present as `dictationRef.current === null`, and treating them the same
    // let a final that arrived after an error insert its text into a box the
    // error had already restored (audit finding, 3 Sep 2026).
    const abortedDictationRef = useRef(false);

    /** Splice `text` into the interim range. Returns the caret position after it. */
    const applyInterim = useCallback((text: string): number | null => {
        const d = dictationRef.current;
        if (!d) return null;
        const before = d.base.slice(0, d.anchor);
        const after = d.base.slice(d.anchor);
        // Same rule as a one-shot insert: no separator at the start of the line
        // or right after existing whitespace.
        const separator = d.anchor > 0 && !/\s$/.test(before) ? ' ' : '';
        const inserted = text ? separator + text : '';
        d.end = d.anchor + inserted.length;
        const next = before + inserted + after;
        setInternalValue(next);
        setValue(next);
        return d.end;
    }, [setValue]);

    /** Hand the box back to the keyboard, optionally undoing the dictation. */
    const endDictation = useCallback((restore: boolean) => {
        const d = dictationRef.current;
        if (!d) return;
        dictationRef.current = null;
        abortedDictationRef.current = true;
        if (restore) { setInternalValue(d.original); setValue(d.original); }
        scheduleDeferred(() => { textareaRef.current?.focus(); adjustHeight(); });
    }, [setValue, adjustHeight, scheduleDeferred]);

    /**
     * The final transcript.
     *
     * Falls back to `insertAtCaret` when no range is armed AND no dictation was
     * just aborted: the hook can still deliver text from a recording that was
     * never started through the button (and that path is what the one-shot
     * insertion tests measure). A final that arrives after THIS composer's own
     * dictation was aborted is a stray — the error already restored the box,
     * and inserting the late text on top of that would look like the error
     * never happened. It is dropped once, not treated as a fresh one-shot.
     */
    const handleFinalText = useCallback((text: string) => {
        if (!dictationRef.current) {
            if (abortedDictationRef.current) { abortedDictationRef.current = false; return; }
            insertAtCaret(text);
            return;
        }
        const caret = applyInterim(text);
        dictationRef.current = null;
        scheduleDeferred(() => {
            const el = textareaRef.current;
            if (el) { el.focus(); if (caret != null) el.setSelectionRange(caret, caret); }
            adjustHeight();
        });
    }, [insertAtCaret, applyInterim, adjustHeight, scheduleDeferred]);

    // The speaking language defaults to the interface language and can be
    // changed BEFORE recording (dictating English into a Turkish interface is
    // an ordinary request).
    const [micLang, setMicLang] = useState<'tr' | 'en'>(lang);
    useEffect(() => { setMicLang(lang); }, [lang]);

    const voice = useVoiceInput({ api, lang: micLang, onText: handleFinalText });

    /** Dictation owns the textarea while it runs — see `readOnly` below. */
    const dictating = voice.state === 'recording' || voice.state === 'transcribing';

    // A voice error and the state committing back to 'idle' land in the SAME
    // render, but `endDictation(true)` — which restores the pre-recording
    // text and clears `dictationRef` — runs afterward, in a passive effect.
    // `dictating` alone therefore went false one render before the interim
    // text was actually gone, and Send's `!dictating` check briefly allowed
    // submitting it (verification round, 3 Sep 2026, `transcribing-send-allowed`).
    // Reading the ref during render is safe here: it is a plain data ref this
    // component itself owns, not a DOM node awaiting commit.
    const sendBlockedByDictation = dictating || (Boolean(voice.error) && dictationRef.current !== null);

    // Every partial replaces the interim range in place.
    useEffect(() => {
        if (voice.state !== 'recording' || !dictationRef.current) return;
        const caret = applyInterim(voice.partialText);
        // setTimeout(0) for the same reason as the one-shot insert: the caret
        // can only be moved after React has written the value, or the
        // controlled textarea's re-render throws the selection to the end.
        scheduleDeferred(() => {
            const el = textareaRef.current;
            if (el && caret != null) {
                el.setSelectionRange(caret, caret);
                // A long dictation grows past the visible box; without this the
                // words being spoken scroll out of sight.
                el.scrollTop = el.scrollHeight;
            }
            adjustHeight();
        });
    }, [voice.partialText, voice.state, applyInterim, adjustHeight, scheduleDeferred]);

    // A failure means the interim text is not going to be confirmed by
    // anything, so it must not be left in the box looking like input.
    useEffect(() => {
        if (voice.error) endDictation(true);
    }, [voice.error, endDictation]);
    // Empty `api` means the backend address has not been resolved yet, so there
    // is nothing to post to.
    const micBlocked = !api || disabled;

    const handleMicClick = () => {
        voice.clearError();  // a previous error clears on the next attempt
        if (voice.state === 'recording') { void voice.stop(); return; }
        if (voice.state !== 'idle') return;
        const ta = textareaRef.current;
        const original = ta ? ta.value : internalValue;
        const start = ta && ta.selectionStart != null ? ta.selectionStart : original.length;
        const end = ta && ta.selectionEnd != null ? ta.selectionEnd : start;
        // A selection is REPLACED by what is dictated — the same thing typing
        // would do to it. The untouched original is kept for the restore paths.
        const base = original.slice(0, start) + original.slice(end);
        dictationRef.current = { anchor: start, end: start, base, original };
        // A stray final from an EARLIER aborted dictation must not be dropped
        // silently if it lands during THIS one instead — that final would
        // never reach here anyway (it belongs to a different `dictationRef`
        // than the one this run is about to build), but clearing the flag
        // keeps it from swallowing a genuine future no-button final by mistake.
        abortedDictationRef.current = false;
        if (base !== original) { setInternalValue(base); setValue(base); }
        void voice.start();
    };

    /** Escape, or anything else that means "forget this recording". */
    const cancelDictation = () => {
        voice.cancel();
        endDictation(true);
    };

    const processFile = (file: File) => {
        if (file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const base64Data = e.target?.result as string;
                setAttachments(prev => [...prev, { name: file.name, data: base64Data, type: 'image' }]);
            };
            reader.readAsDataURL(file);
        } else {
            // Non-image external file (experimental)
            setAttachments(prev => [...prev, { name: file.name, data: '', type: 'file' }]);
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        files.forEach(processFile);
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    // Video YEREL dosya: Electron 34'te File.path kaldırıldı → main-process dialog
    // mutlak yolu doğrudan döndürür (base64 YOK — video büyük).
    const pickVideoFile = async () => {
        const ipc = (window as any).ipc;
        if (!ipc) return;
        const picked = await ipc.invoke('open-video-dialog');  // [{path, name}] | null
        if (picked && picked.length) {
            setAttachments(prev => [...prev, ...picked.map((v: any) => ({
                name: v.name, data: '', type: 'video' as const, path: v.path,
            }))]);
        }
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        const items = Array.from(e.clipboardData.items);
        items.forEach(item => {
            if (item.type.startsWith('image/')) {
                const file = item.getAsFile();
                if (file) processFile(file);
            }
        });
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        
        // 1. Internal File Drop (from Sidebar)
        // ⚠️ `useFileSystem.ts`'teki `setData` ile BİREBİR eşleşmek zorunda; ayrışırsa
        // dosya sürükleme sessizce çalışmaz hale gelir (aynı oturum içi, sürüm riski yok).
        const internalData = e.dataTransfer.getData('application/x-gamachine-file');
        if (internalData) {
            try {
                const entry = JSON.parse(internalData);
                if (!entry.isDirectory) {
                    if (onFileDrop) onFileDrop(entry);
                    // Add as attachment immediately
                    setAttachments(prev => {
                        if (prev.some(a => a.path === entry.path)) return prev;
                        return [...prev, { name: entry.name, data: '', type: 'file', path: entry.path }];
                    });
                    return;
                }
            } catch (err) { console.error("Internal drop error:", err); }
        }

        // 2. External File Drop (from OS)
        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) {
            files.forEach(processFile);
        }
    };

    // Sync internal value when parent resets to '' (after submit)
    useEffect(() => {
        if (value === '' || value !== internalValue) {
            setInternalValue(value);
        }
    }, [value]);

    // Built-in (app) komutu + backend'den gelen Claude Code slash komutları
    const commandSuggestions: CommandSuggestion[] = useMemo(() => {
        const builtin: CommandSuggestion[] = [
            { icon: <span>🧠</span>, label: 'Compact', description: t('composer.compactDesc'), prefix: '/compact' },
        ];
        const skillSet = new Set(skills || []);
        const dynamic: CommandSuggestion[] = (slashCommands || []).map(name => ({
            icon: <span>{skillSet.has(name) ? '✨' : '/'}</span>,
            label: name,
            description: '',
            prefix: '/' + name,
            isSkill: skillSet.has(name),
        }));
        return [...builtin, ...dynamic];
    }, [slashCommands, skills]);

    // Yazdıkça filtrele: '/' sonrası metin prefix/label içinde geçenler (perf için ilk 50)
    const filteredSuggestions = useMemo(() => {
        if (!internalValue.startsWith('/')) return [];
        const q = internalValue.slice(1).toLowerCase();
        const list = q
            ? commandSuggestions.filter(c =>
                c.prefix.toLowerCase().includes(q) || c.label.toLowerCase().includes(q))
            : commandSuggestions;
        return list.slice(0, 50);
    }, [internalValue, commandSuggestions]);

    useEffect(() => {
        if (internalValue.startsWith('/') && !internalValue.includes(' ') && filteredSuggestions.length > 0) {
            setShowCommandPalette(true);
            setActiveSuggestion(0);
        } else {
            setShowCommandPalette(false);
        }
    }, [internalValue, filteredSuggestions.length]);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as Node;
            const commandButton = document.querySelector('[data-command-button]');
            if (commandPaletteRef.current && !commandPaletteRef.current.contains(target) && !commandButton?.contains(target)) {
                setShowCommandPalette(false);
            }
            const galleryButton = document.querySelector('[data-gallery-button]');
            if (galleryRef.current && !galleryRef.current.contains(target) && !galleryButton?.contains(target)) {
                setShowSkillsGallery(false);
            }
            if (mentionMenuRef.current && !mentionMenuRef.current.contains(target) && target !== textareaRef.current) {
                setMentionDismissedAt(mentionRef.current?.start ?? null);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    // ——— `@` chat mentions ———
    //
    // The caret decides which `@query` is being typed, so it is tracked on
    // every change and selection move. Esc closes the menu for THAT `@` only
    // (keyed by its position); typing a new `@` opens it again.
    const [caret, setCaret] = useState(0);
    const [mentionDismissedAt, setMentionDismissedAt] = useState<number | null>(null);
    const [activeMention, setActiveMention] = useState(0);
    const mentionMenuRef = useRef<HTMLDivElement>(null);
    const mention = useMemo(() => mentionQueryAt(internalValue, caret), [internalValue, caret]);
    const mentionOptions = useMemo(
        () => (mention ? mentionTargets(chats || [], currentChatId ?? null, mention.query) : []),
        [mention, chats, currentChatId]);
    const showMentionMenu = !!mention && mention.start !== mentionDismissedAt
        && mentionOptions.length > 0 && !showCommandPalette && !dictating;
    const mentionRef = useRef(mention);
    mentionRef.current = mention;
    // The textarea keeps `@<id>` (that is what is sent); this line names them.
    const resolvedMentions = useMemo(() => {
        if (!chats?.length || !internalValue.includes('@')) return [];
        const seen = new Set<number>();
        const out: { id: number; label: string }[] = [];
        for (const m of findMentions(internalValue)) {
            if (seen.has(m.id)) continue;
            seen.add(m.id);
            const chat = chats.find(c => c.id === m.id);
            if (chat) out.push({ id: m.id, label: mentionLabel(m.id, chat.title) });
        }
        return out;
    }, [internalValue, chats]);

    useEffect(() => { setActiveMention(0); }, [mention?.start, mention?.query]);
    useEffect(() => { if (!mention) setMentionDismissedAt(null); }, [mention]);
    // Keeps the keyboard's option visible. Scrolls the list box only: the chat
    // view's scrolling belongs to `useAutoScroll` (auto-scroll.test.tsx).
    useEffect(() => {
        if (!showMentionMenu) return;
        const list = mentionMenuRef.current?.querySelector<HTMLElement>('[role="listbox"]');
        const item = list?.querySelector<HTMLElement>('[aria-selected="true"]');
        if (!list || !item) return;
        if (item.offsetTop < list.scrollTop) list.scrollTop = item.offsetTop;
        else if (item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight) {
            list.scrollTop = item.offsetTop + item.offsetHeight - list.clientHeight;
        }
    }, [showMentionMenu, activeMention]);

    const syncCaret = (el: HTMLTextAreaElement) => setCaret(el.selectionStart ?? el.value.length);

    const pickMention = (id: number) => {
        const m = mentionRef.current;
        if (!m) return;
        const ta = textareaRef.current;
        const base = ta ? ta.value : internalValue;
        const end = m.start + 1 + m.query.length;
        const rest = base.slice(end);
        // Picked mid-text before a space: reuse it rather than doubling it.
        const inserted = /^\s/.test(rest) ? `@${id}` : `@${id} `;
        const next = base.slice(0, m.start) + inserted + rest;
        const at = m.start + `@${id} `.length;
        setInternalValue(next);
        setValue(next);
        setCaret(at);
        scheduleDeferred(() => {
            const el = textareaRef.current;
            if (el) { el.focus(); el.setSelectionRange(at, at); }
            adjustHeight();
        });
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // While an IME composes (Turkish/Japanese/Chinese input), Enter, Tab
        // and arrows confirm or move its candidate; acting on them here picked
        // a mention, a command or sent the message mid-word (Codex
        // mentionaudit, 27 Sep 2026). keyCode 229 is what some browsers report
        // instead of isComposing.
        if (e.nativeEvent.isComposing || e.keyCode === 229) return;
        if (showMentionMenu) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActiveMention(prev => (prev + 1) % mentionOptions.length);
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActiveMention(prev => (prev - 1 + mentionOptions.length) % mentionOptions.length);
                return;
            }
            if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
                e.preventDefault();
                const target = mentionOptions[activeMention] ?? mentionOptions[0];
                if (target) pickMention(target.id);
                return;
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                setMentionDismissedAt(mention!.start);
                return;
            }
        }
        if (dictating) {
            // The box belongs to dictation while it runs. Escape drops the
            // recording; everything else is ignored — Enter especially, which
            // would otherwise send a half-transcribed sentence.
            if (e.key === 'Escape' && voice.state === 'recording') {
                e.preventDefault();
                cancelDictation();
            }
            return;
        }
        if (showCommandPalette) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActiveSuggestion(prev => prev < filteredSuggestions.length - 1 ? prev + 1 : 0);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActiveSuggestion(prev => prev > 0 ? prev - 1 : filteredSuggestions.length - 1);
            } else if (e.key === 'Tab' || e.key === 'Enter') {
                e.preventDefault();
                if (activeSuggestion >= 0 && filteredSuggestions[activeSuggestion]) {
                    const selectedCommand = filteredSuggestions[activeSuggestion];
                    setInternalValue(selectedCommand.prefix + ' ');
                    setShowCommandPalette(false);
                }
            } else if (e.key === 'Escape') {
                e.preventDefault();
                setShowCommandPalette(false);
            }
        } else if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (internalValue.trim() || attachments.length > 0) handleSendMessage();
        }
    };

    const handleSendMessage = async () => {
        // While a turn runs the page queues the message instead of sending it.
        if (internalValue.trim() || attachments.length > 0) {
            const trimmed = internalValue.trim();
            if (trimmed.startsWith('/') && onCommand) {
                const handled = onCommand(trimmed);
                if (handled) {
                    setInternalValue('');
                    setValue('');
                    adjustHeight(true);
                    return;
                }
            }
            let finalMsg = internalValue;
            const images = attachments.filter(a => a.type === 'image').map(a => a.data);
            const videos = attachments
                .filter(a => a.type === 'video')
                .map((a: any) => a.url ? { kind: 'url', url: a.url, name: a.name }
                                       : (a.path ? { kind: 'path', path: a.path, name: a.name } : null))
                .filter(Boolean);
            const fileAttachments = attachments.filter(a => a.type === 'file');

            if (fileAttachments.length > 0) {
                // If there are file attachments, we need their content. 
                // Since AnimatedChatInput doesn't have IPC, home.tsx will handle the content loading 
                // via onSendMessage or we can pass the paths and let the parent handle it.
                // For now, let's just pass images and let the parent see the paths if needed.
                // But wait, the onSendMessage signature only takes (val, images).
                // I'll update it in home.tsx to handle attachments too or just append paths to finalMsg.
                const paths = fileAttachments.filter(a => a.path).map(a => `[File Attached: ${a.path}]`).join('\n');
                if (paths) finalMsg += (finalMsg ? '\n\n' : '') + paths;
            }

            onSendMessage(finalMsg, images, videos);
            setInternalValue("");
            setValue("");
            setAttachments([]);
            adjustHeight(true);
        }
    };

    // A queued message taken back for editing. Text already in the box is
    // kept: the message is added after it instead of replacing it.
    const takeBackQueued = (item: QueueItemView) => {
        const base = internalValue.trim() ? `${internalValue}\n\n${item.draft}` : item.draft;
        setInternalValue(base);
        setValue(base);
        const restored = [
            ...(item.images ?? []).map((data, i) => ({ name: `image-${i + 1}`, data, type: 'image' as const })),
            ...(item.videos ?? []).map((v: any) => ({
                name: v.name || v.url || v.path || 'video', data: '', type: 'video' as const, path: v.path, url: v.url,
            })),
        ];
        if (restored.length > 0) setAttachments(prev => [...prev, ...restored]);
        scheduleDeferred(() => { textareaRef.current?.focus(); adjustHeight(); });
    };
    const hasDraft = Boolean(internalValue.trim()) || attachments.length > 0;

    return (
        <motion.div 
            className={cn("relative backdrop-blur-2xl bg-black rounded-2xl border border-white/[0.08] shadow-2xl transition-all duration-300", 
                inputFocused ? "border-white/20 shadow-white/[0.02]" : "",
                className)}
            initial={{ scale: 0.98 }}
            animate={{ scale: 1 }}
        >
            <AnimatePresence>
                {showCommandPalette && (
                    <motion.div 
                        ref={commandPaletteRef}
                        className="absolute left-4 right-4 bottom-full mb-2 backdrop-blur-xl bg-black rounded-lg z-50 shadow-lg border border-white/10 overflow-hidden"
                        initial={{ opacity: 0, y: 5 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 5 }}
                    >
                        <div className="py-1 bg-black max-h-[280px] overflow-y-auto">
                            {filteredSuggestions.map((suggestion, index) => (
                                <div
                                    key={suggestion.prefix}
                                    className={cn(
                                        "flex items-center gap-2 px-3 py-2 text-xs transition-colors cursor-pointer",
                                        activeSuggestion === index ? "bg-white/10 text-white" : "text-white/70 hover:bg-white/5"
                                    )}
                                    onClick={() => {
                                        setInternalValue(suggestion.prefix + ' ');
                                        setShowCommandPalette(false);
                                    }}
                                >
                                    <div className="w-5 h-5 flex items-center justify-center text-white/60">{suggestion.icon}</div>
                                    <div className="font-medium text-[11px]">{suggestion.label}</div>
                                    {suggestion.isSkill && (
                                        <span className="text-[8px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-300 border border-violet-500/20">skill</span>
                                    )}
                                    <div className="text-white/40 text-[10px] ml-auto">{suggestion.prefix}</div>
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {showMentionMenu && (
                    <motion.div
                        ref={mentionMenuRef}
                        data-testid="mention-menu"
                        className="absolute left-4 right-4 bottom-full mb-2 backdrop-blur-xl bg-black rounded-lg z-50 shadow-lg border border-white/10 overflow-hidden"
                        initial={{ opacity: 0, y: 5 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 5 }}
                    >
                        <div className="px-3 pt-2 pb-1 text-[9px] font-semibold uppercase tracking-wider text-white/30">
                            {t('mention.menuTitle')}
                        </div>
                        <div role="listbox" aria-label={t('mention.menuTitle')} className="relative pb-1 bg-black max-h-[280px] overflow-y-auto">
                            {mentionOptions.map((target, index) => (
                                <div
                                    key={target.id}
                                    role="option"
                                    aria-selected={activeMention === index}
                                    data-testid={`mention-option-${target.id}`}
                                    // Keep the textarea focused, or the caret the pick needs is lost.
                                    onMouseDown={(e) => e.preventDefault()}
                                    onMouseEnter={() => setActiveMention(index)}
                                    onClick={() => pickMention(target.id)}
                                    className={cn(
                                        "flex items-center gap-2 px-3 py-1.5 text-xs transition-colors cursor-pointer min-w-0",
                                        activeMention === index ? "bg-white/10 text-white" : "text-white/70 hover:bg-white/5"
                                    )}
                                >
                                    <span className="font-mono text-[10px] text-white/40 shrink-0 w-10">#{target.id}</span>
                                    <span className="font-medium text-[11px] truncate">{target.title}</span>
                                    {target.parentId != null && (
                                        <>
                                            <span className="text-[8px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/[0.06] text-white/50 border border-white/10 shrink-0">
                                                {t('mention.branch')}
                                            </span>
                                            <span className="text-white/30 text-[10px] truncate ml-auto">
                                                {t('mention.branchOf', { no: target.parentId, ad: target.parentTitle ?? '' })}
                                            </span>
                                        </>
                                    )}
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Skills & Komutlar galerisi (açıklamalı, aranabilir katalog) */}
            {showSkillsGallery && (
                <div ref={galleryRef}>
                    <SkillsGallery
                        meta={commandMeta}
                        skills={skills}
                        provider={galleryProvider}
                        onSelect={handleSelectCommand}
                        onClose={() => setShowSkillsGallery(false)}
                    />
                </div>
            )}

            {queue && <MessageQueue {...queue} onEditTake={takeBackQueued} />}

            <div className="p-3">
                <Textarea
                    ref={textareaRef}
                    value={internalValue}
                    onChange={(e) => {
                        setInternalValue(e.target.value);
                        syncCaret(e.target);
                        adjustHeight();
                    }}
                    onSelect={(e) => syncCaret(e.currentTarget)}
                    onKeyDown={handleKeyDown}
                    onPaste={handlePaste}
                    onDrop={handleDrop}
                    onDragOver={(e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'copy';
                    }}
                    onFocus={() => setInputFocused(true)}
                    onBlur={() => setInputFocused(false)}
                    placeholder={disabled ? disabledPlaceholder : placeholder}
                    containerClassName="w-full"
                    disabled={disabled}
                    // readOnly, not disabled: a disabled textarea loses focus
                    // and cannot hold a selection, and the live transcript is
                    // written by moving the caret inside this element.
                    readOnly={dictating}
                    className={cn(
                        "w-full px-3 py-2 resize-none bg-transparent border-none text-[13px] focus:outline-none min-h-[40px] custom-scrollbar",
                        disabled ? "text-orange-500/50 cursor-not-allowed italic" : "text-white/90 placeholder:text-white/20"
                    )}
                    // maxHeight (200px) aşıldığında textarea içinde mouse/trackpad ile
                    // scroll yapılabilsin diye overflow-y: auto (önceden hidden'dı → metin kilitleniyordu)
                    style={{ overflowY: "auto" }}
                    showRing={false}
                />
                {resolvedMentions.length > 0 && (
                    <div
                        data-testid="mention-resolved"
                        aria-label={t('mention.resolved')}
                        title={t('mention.resolved')}
                        className="px-3 flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] text-slate-500"
                    >
                        {resolvedMentions.map(m => (
                            <span key={m.id} className="whitespace-nowrap">
                                <span className="font-mono">@{m.id}</span> → <span className="text-blue-300/80">{m.label}</span>
                            </span>
                        ))}
                    </div>
                )}
            </div>

            <AnimatePresence>
                {attachments.length > 0 && (
                    <div className="px-3 pb-3 flex gap-2 overflow-x-auto custom-scrollbar no-scrollbar py-2 border-t border-white/[0.03]">
                        {attachments.map((file, index) => (
                            <motion.div 
                                key={index} 
                                initial={{ opacity: 0, scale: 0.8, x: -10 }}
                                animate={{ opacity: 1, scale: 1, x: 0 }}
                                exit={{ opacity: 0, scale: 0.8 }}
                                className="relative flex-shrink-0 group"
                            >
                                {file.type === 'image' ? (
                                    <img src={file.data} alt="preview" className="w-14 h-14 object-cover rounded-lg border border-white/10 shadow-md" />
                                ) : file.type === 'video' ? (
                                    <div className="w-14 h-14 flex flex-col items-center justify-center bg-white/5 rounded-lg border border-white/10 shadow-md px-1 overflow-hidden" title={file.name}>
                                        <span className="text-lg leading-none mb-0.5">🎬</span>
                                        <span className="text-[8px] text-white/50 truncate w-full text-center">{file.url ? 'URL' : file.name}</span>
                                    </div>
                                ) : (
                                    <div className="w-14 h-14 flex flex-col items-center justify-center bg-white/5 rounded-lg border border-white/10 shadow-md px-1 overflow-hidden">
                                        <FileUp className="w-5 h-5 text-blue-400 mb-0.5" />
                                        <span className="text-[8px] text-white/50 truncate w-full text-center">{file.name}</span>
                                    </div>
                                )}
                                <button 
                                    onClick={() => setAttachments(prev => prev.filter((_, i) => i !== index))} 
                                    className="absolute -top-1.5 -right-1.5 bg-red-500/90 text-white rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition-opacity z-10 hover:bg-red-600"
                                >
                                    <XIcon className="w-3 h-3" />
                                </button>
                            </motion.div>
                        ))}
                    </div>
                )}
            </AnimatePresence>

            <div className="p-2 border-t border-white/[0.05] flex items-center justify-between gap-4 bg-white/[0.01] rounded-b-2xl">
                <div className="flex items-center gap-2">
                    <input 
                        type="file" 
                        ref={fileInputRef} 
                        onChange={handleFileChange} 
                        accept="image/*" 
                        className="hidden" 
                        multiple 
                    />
                    <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="p-2 rounded-lg text-white/40 hover:text-white/90 hover:bg-white/5 transition-colors"
                        title={t('composer.addImage')}
                    >
                        <PlusIcon className="w-3.5 h-3.5" />
                    </button>
                    <button
                        type="button"
                        onClick={pickVideoFile}
                        className="p-2 rounded-lg text-white/40 hover:text-white/90 hover:bg-white/5 transition-colors text-sm leading-none"
                        title={t('composer.addVideo')}
                    >🎬</button>
                    <button
                        type="button"
                        data-mic-button
                        onClick={handleMicClick}
                        disabled={micBlocked || voice.state === 'transcribing'}
                        aria-pressed={voice.state === 'recording'}
                        aria-label={voice.state === 'recording' ? t('mic.stop') : t('mic.start')}
                        title={
                            micBlocked ? t('mic.err.server')
                                : voice.state === 'recording' ? t('mic.stop')
                                : voice.state === 'transcribing' ? t('mic.transcribing')
                                : t('mic.start')
                        }
                        className={cn(
                            "p-2 rounded-lg transition-colors flex items-center gap-1",
                            voice.state === 'recording'
                                ? "text-red-400 bg-red-500/10"
                                : voice.state === 'transcribing'
                                    ? "text-violet-300 bg-violet-500/10 animate-pulse"
                                    : "text-white/40 hover:text-white/90 hover:bg-white/5",
                            micBlocked ? "opacity-40 cursor-not-allowed" : ""
                        )}
                    >
                        <Mic className="w-3.5 h-3.5" />
                        {voice.state === 'recording' && (
                            <>
                                <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                                <span className="text-[10px] tabular-nums">{formatElapsed(voice.elapsedMs)}</span>
                            </>
                        )}
                    </button>
                    <button
                        type="button"
                        data-mic-lang
                        onClick={() => setMicLang(p => (p === 'tr' ? 'en' : 'tr'))}
                        disabled={voice.state !== 'idle'}
                        title={t('mic.lang.toggle', { lang: micLang })}
                        className="text-[10px] px-1 rounded text-white/40 hover:text-white/90 hover:bg-white/5 transition-colors uppercase"
                    >{micLang}</button>
                    {voice.error && (
                        // Inline text, NOT a toast: the error has to stay next
                        // to the button so it is still visible while the user
                        // tries again. The raw detail goes in `title` — no
                        // jargon on screen.
                        <span
                            data-mic-error
                            className="text-[10px] text-red-400"
                            title={voice.error.detail || t(`mic.err.${voice.error.kind}` as any)}
                        >{t(`mic.err.${voice.error.kind}` as any)}</span>
                    )}
                    {commandMeta.length > 0 && (
                        <button
                            type="button"
                            data-gallery-button
                            onClick={() => { setShowSkillsGallery(v => !v); setShowCommandPalette(false); }}
                            className={cn(
                                "p-2 rounded-lg transition-colors",
                                showSkillsGallery ? "text-violet-300 bg-violet-500/10" : "text-white/40 hover:text-white/90 hover:bg-white/5"
                            )}
                            title={t('skills.title')}
                        >
                            <Sparkles className="w-3.5 h-3.5" />
                        </button>
                    )}
                </div>
                
                <div className="flex items-center gap-2">
                {isLoading && (
                    <button 
                        type="button" 
                        onClick={onStop} 
                        className="px-4 py-1.5 rounded-lg text-[10px] font-bold transition-all flex items-center gap-2 bg-red-500 text-white hover:bg-red-600 shadow-[0_0_15px_rgba(239,68,68,0.3)] animate-pulse"
                    >
                        <Square className="w-3 h-3 fill-current" />
                        <span>{t('composer.stop')}</span>
                    </button>
                )}
                {/* While a turn runs, Send appears only with something to queue. */}
                {(!isLoading || hasDraft) && (
                    <button 
                        type="button" 
                        onClick={handleSendMessage} 
                        // `sendBlockedByDictation`, not just `dictating`: the box is
                        // still showing unconfirmed interim text while `transcribing`
                        // too, AND for one more render after an error commits state
                        // back to `idle` but before the restore effect has run — Send
                        // used to stay enabled through both windows (audit findings,
                        // 3 Sep 2026).
                        disabled={disabled || sendBlockedByDictation || !hasDraft}
                        data-send-button
                        title={isLoading ? t('queue.addHint') : undefined}
                        className={cn(
                            "px-4 py-1.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-1.5",
                            hasDraft && !disabled && !sendBlockedByDictation
                                ? "bg-white text-black hover:bg-white/90 active:scale-95 shadow-lg shadow-white/5" 
                                : "bg-white/[0.05] text-white/20"
                        )}
                    >
                        <SendIcon className="w-3 h-3" />
                        <span>{isLoading ? t('queue.add') : 'Send'}</span>
                    </button>
                )}
                </div>
            </div>
        </motion.div>
    );
}

export function ThinkingIndicator() {
    return (
        <motion.div 
            className="fixed bottom-6 right-6 backdrop-blur-2xl bg-black rounded-full px-4 py-2 shadow-lg border border-white/[0.08] z-50"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
        >
            <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 text-xs text-white/70">
                    <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                    <span>Thinking</span>
                    <TypingDots />
                </div>
            </div>
        </motion.div>
    );
}

export function AnimatedAIChat({ 
    onSendMessage, 
    onStop,
    isLoading 
}: { 
    onSendMessage: (val: string) => void;
    onStop?: () => void;
    isLoading: boolean;
}) {
    const [value, setValue] = useState("");
    const [inputFocused, setInputFocused] = useState(false);

    return (
        <div className="min-h-screen flex flex-col w-full items-center justify-center bg-transparent text-white p-6 relative overflow-hidden">
            <div className="absolute inset-0 w-full h-full overflow-hidden">
                <div className="absolute top-0 left-1/4 w-96 h-96 bg-violet-500/10 rounded-full filter blur-[128px] animate-pulse" />
                <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-indigo-500/10 rounded-full filter blur-[128px] animate-pulse delay-700" />
            </div>
            <div className="w-full max-w-2xl mx-auto relative">
                <div className="text-center space-y-3 mb-12">
                    <h1 className="text-3xl font-medium tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white/90 to-white/40 pb-1">
                        How can I help today?
                    </h1>
                    <p className="text-sm text-white/40">Type a command or ask a question</p>
                </div>

                <AnimatedChatInput 
                    value={value} 
                    setValue={setValue} 
                    onSendMessage={onSendMessage} 
                    onStop={onStop}
                    isLoading={isLoading} 
                />
            </div>
            <AnimatePresence>{isLoading && <ThinkingIndicator />}</AnimatePresence>
        </div>
    );
}

function TypingDots() {
    return (
        <div className="flex items-center ml-1">
            {[1, 2, 3].map((dot) => (
                <motion.div
                    key={dot}
                    className="w-1.5 h-1.5 bg-white/90 rounded-full mx-0.5"
                    initial={{ opacity: 0.3 }}
                    animate={{ 
                        opacity: [0.3, 0.9, 0.3],
                        scale: [0.85, 1.1, 0.85]
                    }}
                    transition={{
                        duration: 1.2,
                        repeat: Infinity,
                        delay: dot * 0.15,
                        ease: "easeInOut",
                    }}
                    style={{
                        boxShadow: "0 0 4px rgba(255, 255, 255, 0.3)"
                    }}
                />
            ))}
        </div>
    );
}

interface ActionButtonProps {
    icon: React.ReactNode;
    label: string;
}

function ActionButton({ icon, label }: ActionButtonProps) {
    const [isHovered, setIsHovered] = useState(false);
    
    return (
        <motion.button
            type="button"
            whileHover={{ scale: 1.05, y: -2 }}
            whileTap={{ scale: 0.97 }}
            onHoverStart={() => setIsHovered(true)}
            onHoverEnd={() => setIsHovered(false)}
            className="flex items-center gap-2 px-4 py-2 bg-neutral-900 hover:bg-neutral-800 rounded-full border border-neutral-800 text-neutral-400 hover:text-white transition-all relative overflow-hidden group"
        >
            <div className="relative z-10 flex items-center gap-2">
                {icon}
                <span className="text-xs relative z-10">{label}</span>
            </div>
            
            <AnimatePresence>
                {isHovered && (
                    <motion.div 
                        className="absolute inset-0 bg-gradient-to-r from-violet-500/10 to-indigo-500/10"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                    />
                )}
            </AnimatePresence>
            
            <motion.span 
                className="absolute bottom-0 left-0 w-full h-0.5 bg-gradient-to-r from-violet-500 to-indigo-500"
                initial={{ width: 0 }}
                whileHover={{ width: "100%" }}
                transition={{ duration: 0.3 }}
            />
        </motion.button>
    );
}

const rippleKeyframes = `
@keyframes ripple {
  0% { transform: scale(0.5); opacity: 0.6; }
  100% { transform: scale(2); opacity: 0; }
}
`;

if (typeof document !== 'undefined') {
    const style = document.createElement('style');
    style.innerHTML = rippleKeyframes;
    document.head.appendChild(style);
}


