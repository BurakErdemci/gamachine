import { Children, isValidElement, memo, useMemo, useRef, useState } from "react";
import { Check, Copy, FileDown, Eye, EyeOff } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";

import { linkTuru, chatUrlTransform, yerelYolaCevir } from "../../lib/chatLink";
import { useLang } from "../../lib/i18n";
import { findMentions, mentionLabel } from "../../lib/chatMentions";
import { holdBackPartialMarkdown } from "../../lib/streamingMarkdown";
import { ChatFileImage } from "./ChatFileImage";

/**
 * `@<id>` in plain text as a chip. A known chat shows its title, the number
 * on hover; an unknown or deleted one keeps `@<id>`. Only the rendering
 * changes: the stored message still says `@<id>`, which is what the backend
 * resolves.
 */
const withMentionChips = (
  children: React.ReactNode,
  titles: ReadonlyMap<number, string>,
  unknownTip: string,
) =>
  Children.map(children, (child) => {
    if (typeof child !== "string") return child;
    const parts: React.ReactNode[] = [];
    let last = 0;
    for (const m of findMentions(child)) {
      const at = m.index;
      if (at > last) parts.push(child.slice(last, at));
      const title = titles.get(m.id);
      const named = !!title?.trim();
      parts.push(title != null ? (
        <span key={at} data-mention={m.id} data-mention-known="" title={named ? `#${m.id} · ${title.trim()}` : `#${m.id}`}
          className="mention">
          {named && <span aria-hidden="true" className="mention-at">@</span>}{mentionLabel(m.id, title)}
        </span>
      ) : (
        <span key={at} data-mention={m.id} title={unknownTip}
          className="mention is-unknown">{m.text}</span>
      ));
      last = at + m.text.length;
    }
    if (last === 0) return child;
    parts.push(child.slice(last));
    return parts;
  });

/**
 * The code plate (mockup `figure.code`): a darker plate on the paper with a header carrying the
 * path, the size, "Open in panel" and "Copy". Highlighting keeps react-syntax-highlighter's
 * Prism tokenizer but drops its inline theme (`useInlineStyles={false}`): the token classes are
 * coloured by the --code-* tokens in thread.css, so every theme paints its own syntax.
 *
 * A block whose first line is `// path: <file>` is a file the agent wrote; it starts folded as
 * before (a long file must not push the conversation away) and can be opened in the workspace.
 */
const CodeBlock = ({ match, codeString, workspacePath, onExportToUnity, onOpenFile, handleCopy, copiedBlock }: any) => {
  const { t } = useLang();
  const isAgentFile = codeString.trim().startsWith("// path:");
  const [isCollapsed, setIsCollapsed] = useState(isAgentFile); // agent files start folded

  let fileName = "";
  let filePath = "";
  if (isAgentFile) {
    const firstLine = codeString.split('\n')[0];
    filePath = firstLine.replace("// path:", "").trim();
    fileName = filePath.split('/').pop() || "Script.cs";
  }
  const dir = filePath ? filePath.slice(0, filePath.length - fileName.length) : "";
  const lineCount = codeString.split('\n').length;
  const copied = copiedBlock === codeString;

  return (
    <figure className="code" data-agent-file={isAgentFile || undefined}>
      <figcaption className="code-head">
        <span className="code-path">
          {isAgentFile ? <>{dir}<b>{fileName}</b></> : <b>{match[1]}</b>}
        </span>
        <span className="code-diff">{isAgentFile && <span className="add">+{lineCount}</span>}</span>
        {isAgentFile && onOpenFile && (
          <button type="button" className="code-copy code-open" onClick={() => onOpenFile(filePath)}>
            <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M11 4h5v5M16 4l-7 7M14 12v4H4V6h4" /></svg>
            {t('code.openInPanel')}
          </button>
        )}
        {isAgentFile && (
          <button type="button" className="code-copy" onClick={() => setIsCollapsed(!isCollapsed)} aria-expanded={!isCollapsed}>
            {isCollapsed ? <Eye size={14} aria-hidden="true" /> : <EyeOff size={14} aria-hidden="true" />}
            {isCollapsed ? t('md.showCode') : t('md.hide')}
          </button>
        )}
        {match[1] === "csharp" && !isAgentFile && workspacePath && onExportToUnity && (
          <button type="button" className="code-copy" data-guide="code-block-actions" onClick={() => onExportToUnity(codeString)} title={t('md.exportUnity')} aria-label={t('md.exportUnity')}>
            <FileDown size={14} aria-hidden="true" />
          </button>
        )}
        {!isCollapsed && (
          <button type="button" className="code-copy" onClick={() => handleCopy(codeString)} aria-label={t('code.copyAria')} title={t('md.copy')}>
            {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            {copied ? t('md.copied') : t('md.copy')}
          </button>
        )}
      </figcaption>
      {!isCollapsed && (
        <SyntaxHighlighter
          language={match[1]}
          useInlineStyles={false}
          // Without this the library still puts its default theme's inline font and colours on
          // <code> (its default `codeTagProps`), whatever `useInlineStyles` says.
          codeTagProps={{ className: `language-${match[1]}` }}
          showLineNumbers
          PreTag="pre"
          className="code-body custom-scrollbar"
        >
          {codeString}
        </SyntaxHighlighter>
      )}
    </figure>
  );
};

// The plain text of a hast node: what a copy button of a language-less block puts on the clipboard.
const hastText = (n: any): string =>
  !n ? "" : n.type === "text" ? String(n.value ?? "") : (n.children ?? []).map(hastText).join("");

// Module-level so a paragraph can tell it holds one: the image is a <figure>, which
// React refuses inside a <p>.
const MarkdownImage = ({ src, alt }: any) => <ChatFileImage src={typeof src === "string" ? src : undefined} alt={alt} />;

const MarkdownRendererInner = ({
  content,
  workspacePath,
  onExportToUnity,
  onOpenFile,
  mentionTitles,
  streaming,
}: {
  content: string;
  workspacePath?: string | null;
  onExportToUnity?: (code: string) => void;
  /** Yerel bir dosya linkine tıklanınca çağrılır; verilmezse link ÖLÜ kalır
      (yönlendirme yapmaz). Opsiyonel, çünkü `SlashCommandCard` gibi editörü
      olmayan bağlamlar da bu bileşeni kullanıyor. */
  onOpenFile?: (path: string) => void;
  /** Given only for the user's own bubbles: their `@<id>` mentions become chips. */
  mentionTitles?: ReadonlyMap<number, string>;
  /** The text is still arriving: an unfinished link on its last line is shown as its label only. */
  streaming?: boolean;
}) => {
  const { t } = useLang();
  const [copiedBlock, setCopiedBlock] = useState<string | null>(null);

  const handleCopy = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedBlock(code);
    setTimeout(() => setCopiedBlock(null), 2000);
  };

  const live = useRef({ t, mentionTitles, onOpenFile, workspacePath, onExportToUnity, handleCopy, copiedBlock });
  live.current = { t, mentionTitles, onOpenFile, workspacePath, onExportToUnity, handleCopy, copiedBlock };
  const hasMentions = Boolean(mentionTitles);
  const components = useMemo(() => ({
        /**
         * ⚠️ Bu override OLMADAN sohbetteki her yerel link uygulamayı
         * boşaltıyordu (ölçüldü 2 Ağu 2026): `href` uygulamanın kendi
         * origin'ine çözülüyor, Electron'un `will-navigate` politikası
         * origin-içi adresleri bilerek geçiriyor ve tek pencere o adrese
         * gidiyor. Çökme değil, NAVİGASYON — bu yüzden hata sınırı da
         * yakalayamıyordu.
         *
         * Dış linkler (`http(s)`, `mailto`) BİLEREK dokunulmadan geçiyor:
         * onları `will-navigate` zaten `preventDefault` + `shell.openExternal`
         * ile doğru yere gönderiyor. Buraya ikinci bir mekanizma koymak,
         * çalışan bir yolu kopyalamak olurdu.
         */
        // `node` BİLEREK ayıklanıyor: react-markdown v10 onu her bileşene
        // geçiriyor ve DOM elemanına yayılırsa React "unrecognized prop"
        // uyarısı basıyor. Uyarı gürültüsü, gerçek uyarıları görünmez yapar.
        a({ href, children, node: _node, ...props }: any) {
          const tur = linkTuru(href);
          // Dış link ve sayfa-içi çapa: dokunulmuyor, ikisi de meşru.
          if (tur === 'dis' || tur === 'capa') {
            return <a href={href} {...props}>{children}</a>;
          }
          return (
            <a
              href={href}
              className={tur === 'yerel-dosya' ? 'chip-file' : undefined}
              data-guide={tur === 'yerel-dosya' ? 'file-chip' : undefined}
              onClick={(e) => {
                // `preventDefault` KOŞULSUZ — üç ayrı sebeple:
                //   1. `live.current.onOpenFile` verilmemiş olabilir (editörsüz bağlamlar,
                //      örn. `SlashCommandCard`); iptali ona bağlamak
                //      düzeltmenin kendisinde bir kenar bırakırdı.
                //   2. `tur === 'bos'` olabilir: boş `href` MEVCUT dokümana
                //      çözülür, yani tıklama sayfayı yeniden yükler. Arızanın
                //      ölçülen son hâli tam olarak buydu.
                //   3. Açılacak dosya bulunamasa bile pencere boşalmamalı.
                e.preventDefault();
                // `yerelYolaCevir`: href yüzde-kodlu geliyor (ölçüldü), ham
                // hâliyle gönderilse dosya diskte bulunamazdı.
                if (tur === 'yerel-dosya') live.current.onOpenFile?.(yerelYolaCevir(String(href)));
              }}
              {...props}
            >
              {children}
            </a>
          );
        },
        // Tablo panelden taşmasın: kendi kartında yatay scroll (bkz. globals.css .chat-table)
        table({ children, ...props }: any) {
          return (
            <div className="chat-table custom-scrollbar">
              <table {...props}>{children}</table>
            </div>
          );
        },
        img: MarkdownImage,
        p({ children, node: _node, ...props }: any) {
          const body = live.current.mentionTitles ? withMentionChips(children, live.current.mentionTitles, live.current.t("mention.unknown")) : children;
          return Children.toArray(children).some((c) => isValidElement(c) && c.type === MarkdownImage)
            ? <div {...props}>{body}</div>
            : <p {...props}>{body}</p>;
        },
        ...(live.current.mentionTitles ? {
          li({ children, node: _node, ...props }: any) {
            return <li {...props}>{withMentionChips(children, live.current.mentionTitles, live.current.t("mention.unknown"))}</li>;
          },
        } : {}),
        // A fenced block arrives as <pre><code>; the plate is drawn by CodeBlock, so the <pre>
        // is only a wrapper (a block without a language still gets a plain plate: `.md-pre`).
        // A block without a language gets no CodeBlock header, so it carries its own copy button;
        // icon-only and outside `.code-copy`, so it stays on a narrow (phone) column too.
        pre({ children, node, ...props }: any) {
          const codeEl = node?.children?.[0];
          const classes = [].concat(codeEl?.properties?.className ?? []).join(" ");
          if (/language-\w/.test(classes)) return <div className="md-pre" {...props}>{children}</div>;
          const text = hastText(codeEl).replace(/\n$/, "");
          const copied = live.current.copiedBlock === text;
          return (
            <div className="md-pre md-pre-plain" {...props}>
              <button type="button" className="md-pre-copy" onClick={() => live.current.handleCopy(text)}
                aria-label={live.current.t('code.copyAria')} title={copied ? live.current.t('md.copied') : live.current.t('md.copy')}>
                {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
              </button>
              {children}
            </div>
          );
        },
        code({ inline, className, children, node: _node, ...props }: any) {
          const match = /language-(\w+)/.exec(className || "");
          const codeString = String(children).replace(/\n$/, "");

          return !inline && match ? (
            <CodeBlock 
              match={match} 
              codeString={codeString} 
              workspacePath={live.current.workspacePath}
              onExportToUnity={live.current.onExportToUnity}
              onOpenFile={live.current.onOpenFile}
              handleCopy={live.current.handleCopy}
              copiedBlock={live.current.copiedBlock}
              {...props}
            />
          ) : (
            <code {...props}>
              {children}
            </code>
          );
        },
  }), [hasMentions]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      // ⚠️ Bu prop OLMADAN aşağıdaki override'a BOŞ DİZGE ulaşıyordu.
      // `defaultUrlTransform` mutlak Windows yolunu (`C:\...`) güvensiz bir
      // protokol sanıp siliyor; gerekçesi `chatLink.ts`'te ölçümüyle yazılı.
      urlTransform={chatUrlTransform}
      // One identity for the overrides: functions created inline on every render are new component
      // types to React, so each stream frame unmounted and remounted every paragraph, code block and
      // image under them (the chat image never settled and the thread jumped, 4 Nov 2026). They read
      // the live props from `live`.
      components={components}
    >
      {streaming ? holdBackPartialMarkdown(content) : content}
    </ReactMarkdown>
  );
};

export const MarkdownRenderer = memo(MarkdownRendererInner);
