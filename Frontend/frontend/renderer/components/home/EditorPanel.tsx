// EN ÜSTTE: modüller import sırasına göre değerlendiriliyor. Loader'ın `paths.vs`
// yapılandırması, `@monaco-editor/react` değerlendirilmeden ÖNCE yazılmalı ki
// hiçbir Editor mount'u gömülü CDN varsayılanını yakalayamasın.
import './monaco-loader';
import React from 'react';
import { Editor, DiffEditor } from '@monaco-editor/react';
import { motion } from 'framer-motion';
import { Plus, Activity, Cpu, Sparkles } from 'lucide-react';
import { defineUnityTheme, THEME_NAME } from './monaco-theme';
import { useLang } from '../../lib/i18n';
import { hostWorkspacePath } from '../../lib/backendWorkspacePath';

// Açık dosyanın uzantısına göre Monaco dili — .md/.json/.yaml vb. artık editörde
// açılabildiği için csharp'a sabitlemek yanlış vurgu yapıyordu.
const MONACO_LANG: Record<string, string> = {
  '.cs': 'csharp', '.md': 'markdown', '.json': 'json', '.asmdef': 'json',
  '.xml': 'xml', '.uxml': 'xml', '.yaml': 'yaml', '.yml': 'yaml',
  '.txt': 'plaintext', '.shader': 'cpp', '.hlsl': 'cpp', '.cginc': 'cpp',
  '.compute': 'cpp', '.uss': 'css',
  // Unity YAML asset formatları (metin — editörde yaml olarak açılır)
  '.anim': 'yaml', '.prefab': 'yaml', '.unity': 'yaml', '.mat': 'yaml',
  '.asset': 'yaml', '.controller': 'yaml', '.overridecontroller': 'yaml',
  '.physicmaterial': 'yaml', '.physicsmaterial2d': 'yaml', '.mixer': 'yaml',
  '.rendertexture': 'yaml', '.spriteatlas': 'yaml', '.terrainlayer': 'yaml',
  '.playable': 'yaml', '.signal': 'yaml', '.preset': 'yaml', '.guiskin': 'yaml',
  '.fontsettings': 'yaml', '.flare': 'yaml', '.giparams': 'yaml',
  '.shadervariants': 'yaml', '.mask': 'yaml', '.brush': 'yaml', '.meta': 'yaml',
  '.asmref': 'json', '.inputactions': 'json', '.html': 'html', '.css': 'css',
  '.js': 'javascript', '.csv': 'plaintext', '.ini': 'ini', '.cfg': 'ini',
};

function monacoLangFor(filePath: string | null): string {
  if (!filePath) return 'csharp';
  const dot = filePath.lastIndexOf('.');
  if (dot < 0) return 'plaintext';
  return MONACO_LANG[filePath.slice(dot).toLowerCase()] ?? 'plaintext';
}

// LSP CompletionItemKind → Monaco CompletionItemKind (numaraları farklı enumlar)
const LSP_TO_MONACO_KIND: Record<number, number> = {
  1: 18, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 7, 9: 8, 10: 9, 11: 12, 12: 13,
  13: 15, 14: 17, 15: 27, 16: 19, 17: 20, 18: 21, 19: 23, 20: 16, 21: 14,
  22: 6, 23: 10, 24: 11, 25: 24,
};

/**
 * The workspace-relative spelling of a path the renderer holds.
 *
 * Every `/lsp/*` body carries this spelling and never an absolute one. The
 * backend's `_abs()` joins a relative path to the workspace root it persisted,
 * so the same string addresses the right file whether the backend runs on the
 * host or in a container where the project is bind-mounted at `/workspace`; an
 * absolute path is kept as-is by `_abs()`, which is how a `C:\...` spelling
 * reached a Linux container and resolved to nothing. `/lsp/change` already sent
 * the relative spelling — the three IntelliSense siblings did not, and a rule
 * that one call site follows and its siblings do not is this repo's most
 * expensive defect shape.
 *
 * Relative is preferred over translating through the IPC bridge because it
 * needs no bridge at all: it is correct in both modes with one code path, and
 * cannot fail closed in the middle of a keystroke-rate completion request.
 *
 * Containment is decided on path COMPONENTS, mirroring
 * `main/helpers/workspace-mapping.ts#toBackendPath` (which solves the same
 * problem with `path.relative`). That module cannot be imported here: it pulls
 * in Node's `path`, and this renderer is a plain Next.js `output: 'export'`
 * bundle (`renderer/next.config.js`) with no `path-browserify` and no webpack
 * `resolve.fallback` for it — no other renderer file imports `path`, and
 * adding the first one here would break the renderer build rather than fix a
 * path bug. So the component-boundary rule is restated with string ops
 * instead of `path.relative`; a plain `absolutePath.startsWith(workspacePath)`
 * is a STRING-prefix check, not a path-component one, and treats
 * `/work-two/...` as inside `/work`.
 */
export const workspaceRelativePath = (absolutePath: string, workspacePath: string | null): string => {
  // An already-relative input has nothing to make relative TO — returning it
  // unchanged is the only truthful answer, same as an outside-workspace one.
  // The absolute-path test is inlined rather than reused from `MUTLAK_YOL`
  // below: this keeps the function callable on its own, with no reference
  // outside its own scope.
  if (!workspacePath || !/^([a-zA-Z]:[\\/]|[\\/])/.test(absolutePath)) return absolutePath;

  // POSIX separators, always. `_abs()` joins this to the persisted root, and
  // in Docker that root is inside Linux: `os.path.join('/workspace',
  // 'Assets\\Player.cs')` there is ONE file name containing backslashes, not
  // a path — so a Windows host would still address nothing. Windows itself
  // accepts `/`, so the normalisation costs nothing with Docker off, and the
  // backend already reports diagnostics with `/` (`omnisharp_manager`
  // replaces separators before sending), so both directions now agree.
  const absNorm = absolutePath.replace(/\\/g, '/').replace(/\/+$/, '');
  const wsNorm = workspacePath.replace(/\\/g, '/').replace(/\/+$/, '');
  const prefix = `${wsNorm}/`;
  // A boundary on `/` after the shared root, not a bare string prefix: a
  // sibling folder that merely shares the leading characters of the workspace
  // path (`/work-two` under `/work`, `C:/GameTwo` under `C:/Game`) fails this
  // and falls through to the absolute-path return below, same as a no-workspace input.
  if (!absNorm.startsWith(prefix)) return absolutePath;
  return absNorm.slice(prefix.length);
};

const MUTLAK_YOL = /^([a-zA-Z]:[\\/]|[\\/])/;

/**
 * The return leg: a path the BACKEND named, turned into something the host side
 * can open — or `null`, meaning do not open anything.
 *
 * Two shapes arrive here and they are not the same problem. A relative path
 * (diagnostics report `os.path.relpath` output) is mode-independent: the
 * `read-file` handler joins it to the host workspace, so it must pass through
 * untouched. An absolute path (a definition result, or a diagnostic reported
 * while the backend had no workspace root) is spelled in the backend's view of
 * the disk and is meaningless on the host under Docker — it must be translated,
 * and `null` from the translator means there is no host answer, not "use what
 * you had". With Docker off the translation is identity, so this leg keeps
 * today's behaviour exactly.
 */
export async function hostOpenTarget(backendPath: string | null | undefined): Promise<string | null> {
  if (!backendPath) return null;
  if (!MUTLAK_YOL.test(backendPath)) return backendPath;
  return hostWorkspacePath(backendPath);
}

export interface LspContext {
  apiUrl?: string | null;
  sessionToken?: string | null;
  openedFilePath: string | null;
  workspacePath: string | null;
  openFile?: (path: string) => void;
}

// Bir LSP isteğinin uçuşta kalabileceği en uzun süre. Sunucu tarafında C# analizi
// başlatılamadığında istek uzun süre asılabiliyordu ve istemcide hiçbir üst sınır
// yoktu: Monaco her imleç hareketinde yeni bir hover isteği ürettiği için istekler
// birikiyor, Chromium'un host başına 6 bağlantı sınırına dayanıyor ve editör
// tümden yanıt veremez hale geliyordu (ölçüldü 2026-07-27).
const LSP_TIMEOUT_MS = 8000;

/**
 * The IntelliSense side of the editor, lifted out of the component so the three
 * sibling call sites can be exercised without mounting Monaco. `getCtx` is read
 * per request: auth token and open file both change after mount.
 */
export function createLspBridge(getCtx: () => LspContext) {
  const lspPost = async (ep: string, body: any, token?: any) => {
    const { apiUrl: api, sessionToken: sess } = getCtx();
    if (!api) return null;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LSP_TIMEOUT_MS);
    // Monaco imleç hareket edince kendi CancellationToken'ını iptal ediyor. Bu
    // dinlenmezse iptal edilmiş bir hover'ın HTTP isteği uçuşta kalmaya devam eder.
    const onCancel = token?.onCancellationRequested?.(() => ctrl.abort());
    try {
      const r = await fetch(`${api}/lsp/${ep}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': sess || '' },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
      return r.ok ? r.json() : null;
    } catch { return null; }
    finally {
      clearTimeout(timer);
      onCancel?.dispose?.();
    }
  };

  // The one place the three siblings agree on how a document is named to the
  // backend. Adding a fourth request means calling this, not re-deriving it.
  const docBody = (model: any, position: any) => {
    const { openedFilePath, workspacePath } = getCtx();
    return {
      path: openedFilePath ? workspaceRelativePath(openedFilePath, workspacePath) : openedFilePath,
      text: model.getValue(),
      line: position.lineNumber,
      column: position.column,
    };
  };

  const registerCsProviders = (monaco: any) => {
    // Monaco global'ine kaydolur — HMR/remount'ta çift kayıt olmasın
    if ((window as any).__csProvidersRegistered) return;
    (window as any).__csProvidersRegistered = true;

    monaco.languages.registerCompletionItemProvider('csharp', {
      triggerCharacters: ['.'],
      provideCompletionItems: async (model: any, position: any, _ctx: any, token: any) => {
        const data = await lspPost('completion', docBody(model, position), token);
        const word = model.getWordUntilPosition(position);
        const range = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
                        startColumn: word.startColumn, endColumn: word.endColumn };
        return { suggestions: (data?.items || []).map((it: any) => ({
          label: it.label,
          kind: LSP_TO_MONACO_KIND[it.kind] ?? 18,
          insertText: it.insertText, detail: it.detail, range })) };
      },
    });

    monaco.languages.registerHoverProvider('csharp', {
      provideHover: async (model: any, position: any, token: any) => {
        const data = await lspPost('hover', docBody(model, position), token);
        return data?.contents ? { contents: [{ value: data.contents }] } : null;
      },
    });

    monaco.languages.registerDefinitionProvider('csharp', {
      provideDefinition: async (model: any, position: any, token: any) => {
        const data = await lspPost('definition', docBody(model, position), token);
        if (!data?.location) return null;
        // Cross-model çözümü yerine dosyayı uygulama içinde aç — ama yol
        // BACKEND'in yazımıyla geliyor, host'unkiyle değil.
        const hedef = await hostOpenTarget(data.location.file);
        if (hedef) getCtx().openFile?.(hedef);
        return null;
      },
    });
  };

  return { lspPost, registerCsProviders };
}

interface EditorPanelProps {
  code: string;
  setCode: (code: string) => void;
  openedFilePath: string | null;
  isEditorFocused: boolean;
  setIsEditorFocused: (focused: boolean) => void;
  workspacePath: string | null;
  problems?: any[];
  // OmniSharp IntelliSense (completion/hover/definition) için backend erişimi
  apiUrl?: string | null;
  sessionToken?: string | null;
  openFile?: (path: string) => void;
  // Diff Mode Props
  diffFile: { name: string; code: string; originalCode?: string; suggestedPath: string } | null;
}

export const EditorPanel: React.FC<EditorPanelProps> = ({
  code,
  setCode,
  openedFilePath,
  isEditorFocused,
  setIsEditorFocused,
  workspacePath,
  problems = [],
  apiUrl,
  sessionToken,
  openFile,
  diffFile
}) => {
  const { t } = useLang();
  const monacoRef = React.useRef<any>(null);
  const editorRef = React.useRef<any>(null);
  const [modelChangedTrigger, setModelChangedTrigger] = React.useState(0);

  // Provider closure'ları bir kez kaydedilir; güncel prop'ları ref üzerinden görsünler
  // (auth token ve açık dosya mount'tan SONRA değişiyor — closure bayat kalmasın).
  const lspCtxRef = React.useRef<LspContext>({ apiUrl, sessionToken, openedFilePath, workspacePath, openFile });
  React.useEffect(() => {
    lspCtxRef.current = { apiUrl, sessionToken, openedFilePath, workspacePath, openFile };
  }, [apiUrl, sessionToken, openedFilePath, workspacePath, openFile]);

  const lspBridgeRef = React.useRef(createLspBridge(() => lspCtxRef.current));

  // Dosya değişince stale marker'ları temizle
  React.useEffect(() => {
    if (monacoRef.current && editorRef.current) {
      const model = editorRef.current.getModel();
      if (model) monacoRef.current.editor.setModelMarkers(model, "owner", []);
    }
  }, [openedFilePath, modelChangedTrigger]);

  React.useEffect(() => {
    if (!monacoRef.current || !editorRef.current) return;
    const model = editorRef.current.getModel();
    if (!model) return;

    if (!problems?.length || !openedFilePath) {
      monacoRef.current.editor.setModelMarkers(model, "owner", []);
      return;
    }

    // Relative path karşılaştırması — hem basename hem relative path eşleştir
    const markers = problems
      .filter(p => {
        if (!p.file) return true;
        const pNorm = p.file.replace(/\\/g, '/');
        const oNorm = openedFilePath.replace(/\\/g, '/');
        return oNorm.endsWith(pNorm) || oNorm.endsWith('/' + pNorm.split('/').pop());
      })
      .map(p => {
        // Hatanın satır içeriğini alarak sınırları belirle (Out-of-bounds koruması)
        const lineCount = model.getLineCount();
        const safeLine = Math.min(Math.max(1, p.line), lineCount);
        const lineContent = model.getLineContent(safeLine) || '';
        const maxCol = lineContent.length + 1; // Monaco'da kolonlar 1 tabanlıdır

        let startCol = p.column;
        if (startCol > maxCol) {
          startCol = Math.max(1, maxCol - 1);
        }

        let endCol = p.endColumn ?? maxCol;
        if (endCol > maxCol) {
          endCol = maxCol;
        }
        if (endCol <= startCol) {
          endCol = startCol + 1;
        }

        return {
          startLineNumber: safeLine,
          startColumn: startCol,
          endLineNumber: safeLine,
          endColumn: endCol,
          message: p.message,
          severity: p.severity?.toLowerCase() === 'error'
            ? monacoRef.current.MarkerSeverity.Error
            : monacoRef.current.MarkerSeverity.Warning
        };
      });

    monacoRef.current.editor.setModelMarkers(model, "owner", markers);
  }, [problems, openedFilePath, modelChangedTrigger]);

  return (
    <div className="flex-1 flex flex-col relative min-w-0 bg-[#000000] border-r border-slate-800/50">
      {/* Diff Mode Overlay */}
      {diffFile ? (
        <div className="flex-1 flex flex-col z-20 bg-black">
          <div className="h-9 flex items-center px-4 bg-blue-600/10 border-b border-blue-500/20">
            <Sparkles size={14} className="text-blue-400 mr-2" />
            <span className="text-[11px] font-bold text-blue-300 uppercase tracking-wider">{t('editor.diffReview', { ad: diffFile.name })}</span>
          </div>
          <div className="flex-1 relative">
            <DiffEditor
              height="100%"
              language="csharp"
              original={diffFile.originalCode || ""}
              modified={diffFile.code}
              theme={THEME_NAME}
              // Defined before the first paint, so the editor never flashes another theme.
              beforeMount={defineUnityTheme}
              options={{
                readOnly: true,
                renderSideBySide: true,
                minimap: { enabled: true },
                fontSize: 13,
                fontFamily: "'JetBrains Mono', monospace",
                lineHeight: 1.5,
                scrollBeyondLastLine: false,
                padding: { top: 20, bottom: 20 },
                renderOverviewRuler: false,
              }}
            />
          </div>
        </div>
      ) : (
        <>
          {!(code || openedFilePath || isEditorFocused) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none z-0">
              <div className="relative">
                <div className="absolute -inset-10 bg-blue-500/5 blur-[100px] rounded-full" />
                <img 
                  src="/images/unity-logo-dark.png" 
                  alt="Unity" 
                  className="w-24 h-24 opacity-5 mb-8 grayscale invert brightness-0"
                  onError={(e) => { (e.target as any).style.display = 'none'; }}
                />
              </div>
              <div className="text-center space-y-2 opacity-30 px-6">
                <h3 className="text-sm font-bold text-slate-400 tracking-[0.2em] uppercase">Gamachine Engine</h3>
                <p className="text-[11px] text-slate-500 max-w-[240px] leading-relaxed">
                  {t('home.editorHint')}
                </p>
              </div>
              <div className="mt-10 flex gap-6 text-[10px] font-mono text-slate-700 font-semibold uppercase tracking-widest pointer-events-none">
                <span className="flex items-center gap-1.5"><Activity size={14} /> {t('home.bugfix')}</span>
                <span className="flex items-center gap-1.5"><Cpu size={14} /> {t('home.codegen')}</span>
                <span className="flex items-center gap-1.5"><Sparkles size={14} /> {t('home.analyze')}</span>
              </div>
            </div>
          )}

          <div
            className={`flex-1 relative z-10 transition-opacity duration-200 ${(code || openedFilePath || isEditorFocused) ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
          >

            <Editor
              height="100%"
              defaultLanguage="csharp"
              language={monacoLangFor(openedFilePath)}
              theme={THEME_NAME}
              beforeMount={defineUnityTheme}
              value={code}
              onChange={(val) => setCode(val || '')}
              onMount={(editor, monaco) => {
                editorRef.current = editor;
                monacoRef.current = monaco;
                lspBridgeRef.current.registerCsProviders(monaco);

                // İlk açılışta marker'ları tetikle
                setModelChangedTrigger(prev => prev + 1);

                // Model değiştiğinde (yeni dosya açıldığında vb.) marker'ları tetikle
                editor.onDidChangeModel(() => {
                  setModelChangedTrigger(prev => prev + 1);
                });

                editor.onDidFocusEditorWidget(() => setIsEditorFocused(true));
                editor.onDidBlurEditorWidget(() => setIsEditorFocused(false));
              }}
              options={{
                minimap: { enabled: false },
                fontSize: 14,
                fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
                scrollBeyondLastLine: false,
                smoothScrolling: true,
                contextmenu: false,
                padding: { top: 24, bottom: 24 },
                lineHeight: 1.6,
                cursorBlinking: "smooth",
                cursorSmoothCaretAnimation: "on",
                formatOnPaste: true,
                "semanticHighlighting.enabled": true
              }}
            />
          </div>
        </>
      )}
    </div>
  );
};
