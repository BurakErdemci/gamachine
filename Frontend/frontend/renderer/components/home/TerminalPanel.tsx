import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import axios from 'axios';
import { useLang, type TKey } from '../../lib/i18n';
import { useAutoScroll } from '../../hooks/home/useAutoScroll';
import { isLight, onThemeChange, readColorTokens, readToken } from '../../lib/themeTokens';

interface TerminalPanelProps {
  id: string;
  isOpen: boolean;
  /** Collapse the drawer to its strip (the PTY keeps running). */
  onClose: () => void;
  /** Open the drawer; the strip's tabs and toggle call this. Defaults to nothing for old callers. */
  onOpen?: () => void;
  workspacePath: string | null;
  problems?: any[];
  /** False until diagnostics have reported once: the strip says nothing rather than "0 errors". */
  problemsKnown?: boolean;
  onProblemClick?: (problem: any) => void;
  apiUrl?: string;
  sessionToken?: string;
  unityConnected?: boolean;
}

const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;

/** Mockup line icons (maket index.html). */
const Ic = ({ children, className = 'ic' }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{children}</svg>
);

// ── xterm colours from the terminal tokens ───────────────────────────────────
const TERM_DEFAULTS = {
  '--term-bg': '#10141e', '--term-text': '#e1e3ea', '--term-dim': '#8c95ab', '--term-prompt': '#8ccfc2',
  '--term-warn': '#e9c98a', '--ed-kw': '#8fb3f0', '--ed-num': '#f2a585', '--ed-line': '#2b3248',
  '--diff-del-mark': '#f2937c', '--diff-add-mark': '#4fd8c8',
} as const;

/**
 * xterm draws on a canvas, so it cannot read CSS: the palette is built from the tokens and
 * rebuilt on a theme switch. The ANSI colours map to the editor's syntax inks, so `git status`
 * reds and greens stay readable on the paper themes' light terminal too; "black" and "white"
 * swap on a light ground, or white text would vanish on it.
 */
export const xtermThemeFromTokens = (tk: Record<keyof typeof TERM_DEFAULTS, string>): ITheme => {
  const light = isLight(tk['--term-bg']);
  return {
    background: tk['--term-bg'],
    foreground: tk['--term-text'],
    cursor: tk['--term-text'],
    cursorAccent: tk['--term-bg'],
    selectionBackground: `${tk['--ed-kw']}55`,
    black: light ? tk['--term-text'] : tk['--ed-line'],
    brightBlack: tk['--term-dim'],
    red: tk['--diff-del-mark'],
    brightRed: tk['--diff-del-mark'],
    green: tk['--term-prompt'],
    brightGreen: tk['--diff-add-mark'],
    yellow: tk['--term-warn'],
    brightYellow: tk['--term-warn'],
    blue: tk['--ed-kw'],
    brightBlue: tk['--ed-kw'],
    magenta: tk['--ed-num'],
    brightMagenta: tk['--ed-num'],
    cyan: tk['--term-prompt'],
    brightCyan: tk['--term-prompt'],
    white: light ? tk['--term-dim'] : tk['--term-text'],
    brightWhite: tk['--term-text'],
  };
};
const currentXtermTheme = () => xtermThemeFromTokens(readColorTokens(TERM_DEFAULTS));

const levelOf = (entry: any): 'error' | 'warning' | 'log' => {
  const t = String(entry?.type || entry?.logType || '').toLowerCase();
  if (t.includes('error') || t.includes('exception')) return 'error';
  if (t.includes('warn')) return 'warning';
  return 'log';
};

// ── Konsol: the Unity console stream ─────────────────────────────────────────
const FILTER_KEY: Record<'all' | 'log' | 'warning' | 'error', TKey> = {
  all: 'terminal.filterAll', log: 'terminal.filterLog', warning: 'terminal.filterWarning', error: 'terminal.filterError',
};

const ConsolePane: React.FC<{ apiUrl?: string; sessionToken?: string; unityConnected?: boolean }> = ({ apiUrl, sessionToken, unityConnected }) => {
  const { t: ceviri } = useLang();
  const [entries, setEntries] = useState<any[]>([]);
  const [filter, setFilter] = useState<'all' | 'log' | 'warning' | 'error'>('all');
  const autoScroll = useAutoScroll();

  useEffect(() => {
    if (!apiUrl || !sessionToken || !unityConnected) return;
    const fetch = async () => {
      try {
        const res = await axios.get(`${apiUrl}/mcp/unity/console`, {
          headers: { 'X-Session-Token': sessionToken }
        });
        if (res.data.connected && Array.isArray(res.data.logs)) {
          setEntries(res.data.logs.slice(-200));
        }
      } catch { /* Unity bağlı değil */ }
    };
    fetch();
    const interval = setInterval(fetch, 3000);
    return () => clearInterval(interval);
  }, [apiUrl, sessionToken, unityConnected]);

  // The scroll moved out of the poll callback: it fired on every 3 s poll whether
  // or not anything new arrived, and unconditionally — reading an older error in
  // this tab was impossible, the view jumped back down three seconds later.
  useEffect(() => {
    autoScroll.followIfPinned();
  }, [entries, autoScroll.followIfPinned]);

  if (!unityConnected) {
    return <p className="prob-note">{ceviri('terminal.unityNoConnectionHint')}</p>;
  }
  const filtered = filter === 'all' ? entries : entries.filter(e => levelOf(e) === filter);
  return (
    <>
      <div className="con-filters">
        {(['all', 'log', 'warning', 'error'] as const).map(f => (
          <button key={f} type="button" className="con-filter" aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {ceviri(FILTER_KEY[f])}
          </button>
        ))}
        <span className="con-count">{ceviri('terminal.entries', { sayi: filtered.length })}</span>
      </div>
      <div className="con-list" onScroll={autoScroll.onScroll} style={{ overflow: 'auto', flex: 1, minHeight: 0 }}>
        {filtered.length === 0 ? (
          <p className="prob-note">{ceviri('terminal.unityConsoleEmpty')}</p>
        ) : filtered.map((entry, i) => (
          <div key={i} className="con-row" data-level={levelOf(entry)}>
            {entry.message || entry.text || JSON.stringify(entry)}
          </div>
        ))}
        <div ref={autoScroll.endRef} />
      </div>
    </>
  );
};

interface TerminalSession {
  id: string;
  name: string;
  cwd: string | null;
}

type DrawerTab = 'terminal' | 'konsol' | 'sorunlar';
const DEFAULT_HEIGHT = 228;

/**
 * The terminal drawer at the foot of the workspace (mockup `.term`, KARAKTER 13B): Terminal /
 * Konsol / Sorunlar. Closed it is a 34 px strip with the tabs and a one-line status; open it is
 * 228 px (resizable, or filling the panel).
 *
 * PTY lifecycle: a shell is spawned the first time the drawer opens and lives as long as the
 * panel. Collapsing the drawer, switching tabs or changing the panel width only hides or resizes
 * its host; xterm is never re-opened. (The old floating panel returned null when closed, which
 * detached xterm from the page: reopening showed an empty box over a shell still running.)
 */
export const TerminalPanel: React.FC<TerminalPanelProps> = ({
  id, isOpen, onClose, onOpen, workspacePath, problems = [], problemsKnown = false, onProblemClick,
  apiUrl, sessionToken, unityConnected
}) => {
  const { t: ceviri } = useLang();
  const [tab, setTab] = useState<DrawerTab>('terminal');
  const [isMaximized, setIsMaximized] = useState(false);
  const [terminalHeight, setTerminalHeight] = useState(DEFAULT_HEIGHT);
  const [isResizing, setIsResizing] = useState(false);
  // #5 katla: the body folds open once per opening.
  const [opening, setOpening] = useState(false);

  // Multi-session Terminal State
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>('');

  const terminalRefs = useRef<{ [id: string]: HTMLDivElement | null }>({});
  const terminalInstancesRef = useRef<{ [id: string]: Terminal }>({});
  const fitAddonsRef = useRef<{ [id: string]: FitAddon }>({});
  const [initializedIds, setInitializedIds] = useState<Set<string>>(new Set());

  const open = () => { onOpen?.(); };
  const pickTab = (next: DrawerTab) => { setTab(next); if (!isOpen) open(); };

  useEffect(() => {
    if (!isOpen) return;
    setOpening(true);
    const id = setTimeout(() => setOpening(false), 260);
    return () => clearTimeout(id);
  }, [isOpen]);

  // Drag the open drawer's top edge. The drawer sits on the window's bottom edge, so its height is
  // the distance from the pointer to the bottom.
  useEffect(() => {
    if (!isResizing) return;
    const move = (e: MouseEvent) => {
      const h = window.innerHeight - e.clientY - 34;
      if (h > 120 && h < window.innerHeight * 0.8) setTerminalHeight(h);
    };
    const up = () => setIsResizing(false);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, [isResizing]);

  // Default session initialization
  useEffect(() => {
    if (isOpen && sessions.length === 0) {
      const initialId = id || `term-${Date.now()}`;
      setSessions([
        { id: initialId, name: 'zsh', cwd: workspacePath }
      ]);
      setActiveSessionId(initialId);
    }
  }, [isOpen, workspacePath, sessions.length, id]);

  // Dynamic initialization for each session
  useEffect(() => {
    if (!isOpen || !ipc) return;

    sessions.forEach(session => {
      const { id: sId, cwd } = session;
      if (initializedIds.has(sId) || !terminalRefs.current[sId]) return;

      // Mark as initialized
      setInitializedIds(prev => {
        const next = new Set(prev);
        next.add(sId);
        return next;
      });

      const term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: readToken('--font-mono') || 'Menlo, Monaco, "Courier New", monospace',
        theme: currentXtermTheme(),
        allowProposedApi: true,
        scrollback: 5000,
      });

      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);
      term.open(terminalRefs.current[sId]!);

      terminalInstancesRef.current[sId] = term;
      fitAddonsRef.current[sId] = fitAddon;

      // Spawn PTY process via Electron background helper
      ipc.invoke('terminal-spawn', { id: sId, cwd }).then((res: any) => {
        if (res.success) {
          ipc.invoke('terminal-write', { id: sId, data: '\r' });
        }
      });

      // IPC listeners
      const unsubscribeData = ipc.on(`terminal-data-${sId}`, (data: string) => {
        term.write(data);
      });

      term.onData((data) => {
        ipc.invoke('terminal-write', { id: sId, data });
      });

      const unsubscribeExit = ipc.on(`terminal-exit-${sId}`, () => {
        term.write(`\r\n${ceviri('terminal.processTerminated')}\r\n`);
      });

      const handleResize = () => {
        try {
          // A hidden host (drawer collapsed, another tab) measures 0: fitting then would shrink
          // the shell to one column and reflow its scrollback.
          const host = terminalRefs.current[sId];
          if (!host?.offsetWidth || !host.offsetHeight) return;
          fitAddon.fit();
          ipc.invoke('terminal-resize', { id: sId, cols: term.cols, rows: term.rows });
        } catch {}
      };

      // The panel changes width (dar / yarim / odak) and the drawer height without any window
      // resize, so the host is observed too.
      const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(handleResize) : null;
      observer?.observe(terminalRefs.current[sId]!);

      // Store cleanup routines
      (term as any)._cleanups = [
        unsubscribeData,
        unsubscribeExit,
        () => {
          observer?.disconnect();
          window.removeEventListener('resize', handleResize);
          term.dispose();
        }
      ];

      window.addEventListener('resize', handleResize);

      setTimeout(() => {
        try {
          handleResize();
          if (sId === activeSessionId) term.focus();
        } catch {}
      }, 150);
    });
  }, [sessions, initializedIds, isOpen, activeSessionId]);

  // xterm cannot read CSS: recolour every shell when the appearance changes.
  useEffect(() => onThemeChange(() => {
    const theme = currentXtermTheme();
    Object.values(terminalInstancesRef.current).forEach(term => { try { term.options.theme = theme; } catch {} });
  }), []);

  // Cleanup removed sessions
  useEffect(() => {
    const sessionIds = new Set(sessions.map(s => s.id));
    initializedIds.forEach(sId => {
      if (!sessionIds.has(sId)) {
        const term = terminalInstancesRef.current[sId];
        if (term) {
          if ((term as any)._cleanups) {
            (term as any)._cleanups.forEach((c: any) => c());
          }
          delete terminalInstancesRef.current[sId];
        }
        delete fitAddonsRef.current[sId];
        delete terminalRefs.current[sId];
        setInitializedIds(prev => {
          const next = new Set(prev);
          next.delete(sId);
          return next;
        });
      }
    });
  }, [sessions, initializedIds]);

  // Clean up all on unmount
  useEffect(() => {
    return () => {
      Object.keys(terminalInstancesRef.current).forEach(sId => {
        const term = terminalInstancesRef.current[sId];
        if (term) {
          if ((term as any)._cleanups) {
            (term as any)._cleanups.forEach((c: any) => c());
          }
        }
      });
      terminalInstancesRef.current = {};
      fitAddonsRef.current = {};
      terminalRefs.current = {};
    };
  }, []);

  // Back on the Terminal tab (or the drawer opened): fit and focus the shell on screen.
  useEffect(() => {
    if (!isOpen || tab !== 'terminal' || !activeSessionId) return;
    const timer = setTimeout(() => {
      try {
        const host = terminalRefs.current[activeSessionId];
        if (host?.offsetWidth && host.offsetHeight) fitAddonsRef.current[activeSessionId]?.fit();
        terminalInstancesRef.current[activeSessionId]?.focus();
      } catch {}
    }, 100);
    return () => clearTimeout(timer);
  }, [isOpen, tab, activeSessionId, terminalHeight, isMaximized]);

  const addSession = () => {
    const newId = `term-${Date.now()}`;
    const newSession = {
      id: newId,
      name: `zsh (${sessions.length + 1})`,
      cwd: workspacePath,
    };
    setSessions(prev => [...prev, newSession]);
    setActiveSessionId(newId);
    setTab('terminal');
  };

  const nextSession = () => {
    if (sessions.length <= 1) return;
    const currentIndex = sessions.findIndex(s => s.id === activeSessionId);
    const nextIndex = (currentIndex + 1) % sessions.length;
    const nextId = sessions[nextIndex].id;
    setActiveSessionId(nextId);
    setTab('terminal');
  };

  const removeSession = (sessionIdToRemove?: string) => {
    const targetId = sessionIdToRemove || activeSessionId;
    if (!targetId) return;

    // Send exit command to the terminal process
    ipc?.invoke('terminal-write', { id: targetId, data: 'exit\r' });

    setSessions(prev => {
      if (prev.length <= 1) {
        // Clear instead of removing if it's the last active session
        const term = terminalInstancesRef.current[targetId];
        term?.clear();
        return prev;
      }

      const filtered = prev.filter(s => s.id !== targetId);

      if (targetId === activeSessionId) {
        const deletedIndex = prev.findIndex(s => s.id === targetId);
        const newActiveIndex = deletedIndex === prev.length - 1 ? deletedIndex - 1 : deletedIndex;
        setTimeout(() => {
          setActiveSessionId(filtered[newActiveIndex].id);
        }, 0);
      }

      return filtered;
    });
  };

  const errors = problems.filter(p => String(p.severity).toLowerCase() === 'error').length;
  const warnings = problems.length - errors;
  const counts = `${ceviri(errors === 1 ? 'terminal.errorOne' : 'terminal.errors', { sayi: errors })} · ${ceviri(warnings === 1 ? 'terminal.warningOne' : 'terminal.warnings', { sayi: warnings })}`;
  const tabs: { id: DrawerTab; label: TKey }[] = [
    { id: 'terminal', label: 'terminal.tabTerminal' },
    { id: 'konsol', label: 'terminal.tabConsole' },
    { id: 'sorunlar', label: 'terminal.tabProblems' },
  ];

  return (
    <div
      className={`term${opening ? ' is-opening' : ''}${isMaximized && isOpen ? ' is-max' : ''}`}
      data-open={isOpen ? 'true' : 'false'}
      data-term={tab}
      data-testid="terminal-drawer"
      style={{ '--term-h': `${terminalHeight}px` } as React.CSSProperties}
    >
      {isOpen && !isMaximized && (
        <div
          className="term-resize"
          role="separator"
          aria-orientation="horizontal"
          aria-label={ceviri('terminal.resize')}
          onMouseDown={(e) => { e.preventDefault(); setIsResizing(true); }}
        />
      )}
      <div className="term-bar">
        <div className="term-tabs" role="tablist" aria-label={ceviri('terminal.drawer')}>
          {tabs.map(({ id: tid, label }) => (
            <button
              key={tid}
              type="button"
              role="tab"
              className="term-tab"
              data-term={tid}
              aria-selected={tab === tid}
              onClick={() => pickTab(tid)}
            >
              {tid === 'terminal' && <Ic className="ic ic-sm"><path d="M4 6l4 4-4 4M10 14.5h6" /></Ic>}
              {ceviri(label)}
              {tid === 'sorunlar' && problems.length > 0 && <span className="term-n num">{problems.length}</span>}
            </button>
          ))}
        </div>
        {problemsKnown && (
          <span className="term-last">
            <span className="tl-full">{ceviri('terminal.statusFull', { durum: counts })}</span>
            <span className="tl-short">{counts}</span>
          </span>
        )}
        <span className="term-acts" style={!problemsKnown ? { marginLeft: 'auto' } : undefined}>
          {tab === 'terminal' && (
            <>
              <button type="button" className="icon-btn" onClick={addSession} aria-label={ceviri('terminal.newSession')} title={ceviri('terminal.newSession')}>
                <Ic><path d="M10 4v12M4 10h12" /></Ic>
              </button>
              {sessions.length > 1 && (
                <button type="button" className="icon-btn" onClick={nextSession} aria-label={ceviri('terminal.nextSession')} title={ceviri('terminal.nextSession')}>
                  <Ic><path d="M8 6l4 4-4 4" /></Ic>
                </button>
              )}
              <button type="button" className="icon-btn" onClick={() => removeSession()} aria-label={ceviri('terminal.killSession')} title={ceviri('terminal.killSession')}>
                <Ic><path d="M4.5 6h11M8 6V4.5h4V6M6 6l.7 10h6.6L14 6" /></Ic>
              </button>
            </>
          )}
          <button type="button" className="icon-btn" onClick={() => setIsMaximized(v => !v)}
            aria-pressed={isMaximized} aria-label={ceviri(isMaximized ? 'terminal.restore' : 'terminal.maximize')} title={ceviri(isMaximized ? 'terminal.restore' : 'terminal.maximize')}>
            <Ic>{isMaximized ? <path d="M6 9l4-4 4 4M6 15l4-4 4 4" /> : <path d="M6 11l4 4 4-4M6 5l4 4 4-4" />}</Ic>
          </button>
        </span>
        <button
          type="button"
          className="icon-btn term-toggle"
          style={!isOpen && !problemsKnown ? { marginLeft: 'auto' } : undefined}
          aria-expanded={isOpen}
          aria-controls={`${id}-body`}
          aria-label={ceviri(isOpen ? 'terminal.close' : 'terminal.open')}
          title={ceviri(isOpen ? 'terminal.close' : 'terminal.open')}
          onClick={() => (isOpen ? onClose() : open())}
        >
          <Ic className="ic ic-sm"><path d="M6 12l4-4 4 4" /></Ic>
        </button>
      </div>

      <div className="term-body" id={`${id}-body`}>
        <div className="term-in">
          {/* Terminal: one host per shell, mounted from the first opening on and only hidden after. */}
          <div className="term-pane" data-term="terminal">
            {sessions.length > 1 && (
              <div className="term-sessions" role="tablist" aria-label={ceviri('terminal.sessions')}>
                {sessions.map(s => (
                  <button key={s.id} type="button" role="tab" className="term-sess" aria-selected={activeSessionId === s.id}
                    onClick={() => { setActiveSessionId(s.id); setTab('terminal'); }}>
                    {s.name}
                  </button>
                ))}
              </div>
            )}
            {sessions.map(session => (
              <div
                key={session.id}
                className="term-host"
                style={{ display: activeSessionId === session.id ? 'block' : 'none' }}
                onClick={() => terminalInstancesRef.current[session.id]?.focus()}
                ref={(el) => { terminalRefs.current[session.id] = el; }}
              />
            ))}
          </div>

          <div className="term-pane" data-term="konsol">
            {/* Polls only while it is on screen, as the old Debug Console tab did. */}
            {isOpen && tab === 'konsol' && (
              <ConsolePane apiUrl={apiUrl} sessionToken={sessionToken} unityConnected={unityConnected} />
            )}
          </div>

          <div className="term-pane" data-term="sorunlar">
            {problems.length === 0 ? (
              <p className="prob-note">{ceviri(problemsKnown ? 'terminal.noProblems' : 'terminal.problemsUnknown')}</p>
            ) : problems.map((prob, i) => (
              <button key={i} type="button" className="prob" data-severity={String(prob.severity).toLowerCase() === 'error' ? 'error' : 'warning'} onClick={() => onProblemClick?.(prob)}>
                <Ic><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></Ic>
                <span className="prob-text">
                  <b>{prob.message}</b>
                  <span className="prob-at">
                    {prob.file && <><span className="prob-file">{String(prob.file).split(/[\\/]/).pop()}</span> · </>}
                    {ceviri('terminal.line', { satir: prob.line, sutun: prob.column })}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
