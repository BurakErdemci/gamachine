// Emniyet ağı: Monaco'yu kullanan her bileşen zaten temayı da import ediyor.
// Loader yapılandırmasını buraya zincirlemek, ileride eklenecek bir üçüncü
// tüketicinin `monaco-loader` import etmeyi unutup sessizce CDN'e düşmesini
// engelliyor — eski hatanın tam olarak bu "yapılandırma yoksa uzaktan yükle"
// sessiz varsayılanı olduğu için önemli. (Tüketiciler ayrıca kendileri de
// import ediyor; oradaki amaç sıralama garantisi, buradaki unutmaya karşı.)
import './monaco-loader';
import type * as Monaco from 'monaco-editor';
import { isLight, onThemeChange, readColorTokens, readToken } from '../../lib/themeTokens';

export const THEME_NAME = 'gamachine';

// The bundled JetBrains Mono webfont (styles/gm/fonts.css) can finish loading
// after Monaco measured its glyphs; a stale measurement puts clicks in the wrong
// column (click at line end, caret lands mid-line). Remeasure once fonts are ready.
let _fontRemeasureHooked = false;
const hookFontRemeasure = (monaco: typeof Monaco) => {
  if (_fontRemeasureHooked || typeof document === 'undefined') return;
  _fontRemeasureHooked = true;
  const remeasure = () => monaco.editor.remeasureFonts();
  const fonts: any = (document as any).fonts;
  fonts?.ready?.then(remeasure).catch(() => {});
  // Geç yüklenen font batch'leri için (ready bir kez çözülür, bu her batch'te tetiklenir)
  fonts?.addEventListener?.('loadingdone', remeasure);
};

/**
 * The editor tokens (tokens.css `--ed-*` / `--diff-*`), with Arena's values as the fallback so
 * a page without the stylesheet (tests, a failed load) still gets a readable dark editor.
 */
export const EDITOR_TOKEN_DEFAULTS = {
  '--ed-bg': '#141925',
  '--ed-head-bg': '#1a2030',
  '--ed-line': '#2b3248',
  '--ed-text': '#e1e3ea',
  '--ed-dim': '#8c95ab',
  '--ed-kw': '#8fb3f0',
  '--ed-type': '#8ccfc2',
  '--ed-fn': '#e9c98a',
  '--ed-num': '#f2a585',
  '--ed-attr': '#b4bccc',
  '--ed-str': '#c9d6a2',
  '--ed-com': '#8c95ab',
  '--ed-tab-mark': '#dddad2',
  '--diff-add-bg': '#193130',
  '--diff-del-bg': '#3a2228',
  '--diff-add-mark': '#4fd8c8',
  '--diff-del-mark': '#f2937c',
} as const;
export type EditorTokens = Record<keyof typeof EDITOR_TOKEN_DEFAULTS, string>;

const bare = (hex: string) => hex.replace('#', '');

/**
 * A Monaco theme drawn from the editor tokens, so the editor speaks the theme's language
 * (KARAKTER 13B) instead of the fixed VS Code palette it had. A light editor ground (Pafta,
 * Atölye) gets Monaco's light base, or its widgets, scrollbars and cursor would stay dark.
 * Translucent colours are the token plus an alpha byte: Monaco accepts `#rrggbbaa`.
 */
export const monacoThemeFromTokens = (tk: EditorTokens): Monaco.editor.IStandaloneThemeData => {
  const light = isLight(tk['--ed-bg']);
  return {
    base: light ? 'vs' : 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: bare(tk['--ed-text']) },
      { token: 'keyword', foreground: bare(tk['--ed-kw']) },
      { token: 'keyword.control', foreground: bare(tk['--ed-kw']) },
      { token: 'type', foreground: bare(tk['--ed-type']) },
      { token: 'type.identifier', foreground: bare(tk['--ed-type']) },
      { token: 'identifier', foreground: bare(tk['--ed-text']) },
      { token: 'number', foreground: bare(tk['--ed-num']) },
      { token: 'string', foreground: bare(tk['--ed-str']) },
      { token: 'string.escape', foreground: bare(tk['--ed-fn']) },
      { token: 'comment', foreground: bare(tk['--ed-com']), fontStyle: 'italic' },
      { token: 'delimiter', foreground: bare(tk['--ed-text']) },
      { token: 'attribute.name', foreground: bare(tk['--ed-attr']) },
      { token: 'attribute.value', foreground: bare(tk['--ed-str']) },
      { token: 'annotation', foreground: bare(tk['--ed-fn']) },
      { token: 'tag', foreground: bare(tk['--ed-kw']) },
      { token: 'key', foreground: bare(tk['--ed-attr']) },
    ],
    colors: {
      'editor.background': tk['--ed-bg'],
      'editor.foreground': tk['--ed-text'],
      'editorGutter.background': tk['--ed-bg'],
      'editorLineNumber.foreground': tk['--ed-dim'],
      'editorLineNumber.activeForeground': tk['--ed-text'],
      'editorCursor.foreground': tk['--ed-tab-mark'],
      'editor.selectionBackground': `${tk['--ed-kw']}40`,
      'editor.inactiveSelectionBackground': `${tk['--ed-kw']}26`,
      'editor.lineHighlightBackground': tk['--ed-head-bg'],
      'editor.lineHighlightBorder': '#00000000',
      'editorIndentGuide.background1': tk['--ed-line'],
      'editorWhitespace.foreground': tk['--ed-line'],
      'editorWidget.background': tk['--ed-head-bg'],
      'editorWidget.border': tk['--ed-line'],
      'editorHoverWidget.background': tk['--ed-head-bg'],
      'editorHoverWidget.border': tk['--ed-line'],
      'editorSuggestWidget.background': tk['--ed-head-bg'],
      'editorSuggestWidget.border': tk['--ed-line'],
      // Monaco colours bracket pairs with its own VS Code palette (red / gold / blue) unless told
      // otherwise; the mockup draws brackets in plain ink, so every level is the editor text.
      'editorBracketHighlight.foreground1': tk['--ed-text'],
      'editorBracketHighlight.foreground2': tk['--ed-text'],
      'editorBracketHighlight.foreground3': tk['--ed-text'],
      'editorBracketHighlight.foreground4': tk['--ed-text'],
      'editorBracketHighlight.foreground5': tk['--ed-text'],
      'editorBracketHighlight.foreground6': tk['--ed-text'],
      'editorBracketHighlight.unexpectedBracket.foreground': tk['--diff-del-mark'],
      'editorBracketMatch.background': `${tk['--ed-kw']}26`,
      'editorBracketMatch.border': tk['--ed-line'],
      'scrollbarSlider.background': `${tk['--ed-dim']}40`,
      'scrollbarSlider.hoverBackground': `${tk['--ed-dim']}66`,
      'diffEditor.insertedLineBackground': tk['--diff-add-bg'],
      'diffEditor.removedLineBackground': tk['--diff-del-bg'],
      'diffEditor.insertedTextBackground': `${tk['--diff-add-mark']}33`,
      'diffEditor.removedTextBackground': `${tk['--diff-del-mark']}33`,
      'diffEditorGutter.insertedLineBackground': tk['--diff-add-bg'],
      'diffEditorGutter.removedLineBackground': tk['--diff-del-bg'],
    },
  };
};

const applyTheme = (monaco: typeof Monaco) => {
  monaco.editor.defineTheme(THEME_NAME, monacoThemeFromTokens(readColorTokens(EDITOR_TOKEN_DEFAULTS)));
  monaco.editor.setTheme(THEME_NAME);
};

export const codeFontFamily = () => readToken('--font-mono') || "'JetBrains Mono', 'Consolas', monospace";
const fontEditors = new Set<{ updateOptions: (options: { fontFamily: string }) => void }>();
export const watchEditorFont = (editor: {
  updateOptions: (options: { fontFamily: string }) => void;
  onDidDispose?: (listener: () => void) => { dispose: () => void };
}) => {
  fontEditors.add(editor);
  editor.updateOptions({ fontFamily: codeFontFamily() });
  const disposed = editor.onDidDispose?.(() => { fontEditors.delete(editor); });
  return () => { fontEditors.delete(editor); disposed?.dispose(); };
};

/**
 * Dispose a DiffEditor's two models only after the editor itself is gone. @monaco-editor/react
 * 4.7.0 disposes the models first on unmount, and monaco-editor 0.55 throws "TextModel got disposed
 * before DiffEditorWidget model got reset" (measured 10 Oct 2026: accepting an approval card crashed
 * the renderer). Pair it with `keepCurrentOriginalModel` / `keepCurrentModifiedModel`, so the
 * library leaves the models alone and this listener frees them.
 */
export const disposeDiffModelsAfterEditor = (editor: {
  getModel?: () => { original?: { dispose: () => void }; modified?: { dispose: () => void } } | null;
  onDidDispose?: (listener: () => void) => unknown;
}) => {
  const models = editor.getModel?.();
  editor.onDidDispose?.(() => {
    models?.original?.dispose();
    models?.modified?.dispose();
  });
};

// Monaco's theme is global (one per page), so one watcher is enough however many editors mount.
let _themeWatched = false;

/**
 * Define the token-driven theme and keep it in step with the appearance: a theme switch
 * re-reads the tokens and redefines it, so every open editor recolours without a reload.
 */
export const defineUnityTheme = (monaco: typeof Monaco) => {
  hookFontRemeasure(monaco);
  applyTheme(monaco);
  if (_themeWatched) return;
  _themeWatched = true;
  onThemeChange(() => {
    applyTheme(monaco);
    for (const editor of fontEditors) {
      // A disposed or broken editor must not prevent other editors from updating.
      try { editor.updateOptions({ fontFamily: codeFontFamily() }); } catch {}
    }
    monaco.editor.remeasureFonts();
  });
};
