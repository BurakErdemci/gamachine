import { Children, memo, useState } from "react";
import { Check, Copy, FileDown, Eye, EyeOff, FileCode } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/cjs/styles/prism";

import { linkTuru, chatUrlTransform, yerelYolaCevir } from "../../lib/chatLink";
import { useLang } from "../../lib/i18n";
import { findMentions } from "../../lib/chatMentions";

/** `@<id>` in plain text as a chip whose tooltip names the chat; the text itself stays as typed. */
const withMentionChips = (children: React.ReactNode, titleOf: (id: number) => string) =>
  Children.map(children, (child) => {
    if (typeof child !== "string") return child;
    const parts: React.ReactNode[] = [];
    let last = 0;
    for (const m of findMentions(child)) {
      const at = m.index;
      if (at > last) parts.push(child.slice(last, at));
      parts.push(
        <span key={at} data-mention={m.id} title={titleOf(m.id)}
          className="px-1 rounded bg-blue-400/15 text-blue-300 font-medium">{m.text}</span>);
      last = at + m.text.length;
    }
    if (last === 0) return child;
    parts.push(child.slice(last));
    return parts;
  });

const CodeBlock = ({ match, codeString, workspacePath, onExportToUnity, handleCopy, copiedBlock }: any) => {
  const { t } = useLang();
  const isAgentFile = codeString.trim().startsWith("// path:");
  const [isCollapsed, setIsCollapsed] = useState(isAgentFile); // Ajan dosyaları varsayılan kapalı

  let fileName = "";
  let filePath = "";
  if (isAgentFile) {
    const firstLine = codeString.split('\n')[0];
    filePath = firstLine.replace("// path:", "").trim();
    fileName = filePath.split('/').pop() || "Script.cs";
  }

  return (
    <div className="relative group my-4">
      {isAgentFile ? (
        <div className={`rounded-xl border transition-all ${isCollapsed ? 'border-slate-800 bg-[#0d1117] hover:border-blue-500/30' : 'border-slate-800 bg-transparent'}`}>
          <div className="flex items-center justify-between px-4 py-2.5 bg-[#0a0c10] rounded-t-xl border-b border-slate-800/50">
            <div className="flex items-center gap-2.5">
              <FileCode size={14} className="text-blue-400" />
              <div className="flex flex-col">
                <span className="text-[11px] font-bold text-slate-200 leading-none">{fileName}</span>
                <span className="text-[9px] text-slate-500 font-mono mt-1">{filePath}</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setIsCollapsed(!isCollapsed)}
                className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-all text-[10px] font-semibold"
              >
                {isCollapsed ? <><Eye size={12} /> {t('md.showCode')}</> : <><EyeOff size={12} /> {t('md.hide')}</>}
              </button>
              {!isCollapsed && (
                <button
                  onClick={() => handleCopy(codeString)}
                  className="p-1.5 rounded-md bg-slate-800/80 hover:bg-slate-700 border border-slate-700/50 text-slate-400 hover:text-slate-200 transition-all"
                  title={t('md.copy')}
                >
                  {copiedBlock === codeString ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                </button>
              )}
            </div>
          </div>
          
          {!isCollapsed && (
            <SyntaxHighlighter
              style={vscDarkPlus}
              language={match[1]}
              PreTag="div"
              className="!bg-[#0a0c10] !m-0 !rounded-b-xl border-none max-h-[500px] overflow-y-auto custom-scrollbar text-[12px]"
            >
              {codeString}
            </SyntaxHighlighter>
          )}
        </div>
      ) : (
        <>
          <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5">
            <span className="text-[10px] text-slate-500 font-mono uppercase">{match[1]}</span>
            {match[1] === "csharp" && workspacePath && onExportToUnity && (
              <button
                onClick={() => onExportToUnity(codeString)}
                className="p-1.5 rounded-md bg-emerald-900/80 hover:bg-emerald-700 border border-emerald-700/50 text-emerald-400 hover:text-emerald-200 transition-all opacity-0 group-hover:opacity-100"
                title={t('md.exportUnity')}
              >
                <FileDown size={13} />
              </button>
            )}
            <button
              onClick={() => handleCopy(codeString)}
              className="p-1.5 rounded-md bg-slate-800/80 hover:bg-slate-700 border border-slate-700/50 text-slate-400 hover:text-slate-200 transition-all opacity-0 group-hover:opacity-100"
              title={t('md.copy')}
            >
              {copiedBlock === codeString ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
            </button>
          </div>
          <SyntaxHighlighter
            style={vscDarkPlus}
            language={match[1]}
            PreTag="div"
            className="rounded-xl !bg-[#0a0c10] border border-slate-800 shadow-lg !pt-10 max-h-[500px] overflow-y-auto custom-scrollbar"
          >
            {codeString}
          </SyntaxHighlighter>
        </>
      )}
    </div>
  );
};

const MarkdownRendererInner = ({
  content,
  workspacePath,
  onExportToUnity,
  onOpenFile,
  mentionTitles,
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
}) => {
  const { t } = useLang();
  const [copiedBlock, setCopiedBlock] = useState<string | null>(null);
  const mentionTitle = (id: number) => {
    const title = mentionTitles?.get(id);
    return title != null ? `#${id} · ${title}` : t("mention.unknown");
  };

  const handleCopy = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedBlock(code);
    setTimeout(() => setCopiedBlock(null), 2000);
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      // ⚠️ Bu prop OLMADAN aşağıdaki override'a BOŞ DİZGE ulaşıyordu.
      // `defaultUrlTransform` mutlak Windows yolunu (`C:\...`) güvensiz bir
      // protokol sanıp siliyor; gerekçesi `chatLink.ts`'te ölçümüyle yazılı.
      urlTransform={chatUrlTransform}
      components={{
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
              onClick={(e) => {
                // `preventDefault` KOŞULSUZ — üç ayrı sebeple:
                //   1. `onOpenFile` verilmemiş olabilir (editörsüz bağlamlar,
                //      örn. `SlashCommandCard`); iptali ona bağlamak
                //      düzeltmenin kendisinde bir kenar bırakırdı.
                //   2. `tur === 'bos'` olabilir: boş `href` MEVCUT dokümana
                //      çözülür, yani tıklama sayfayı yeniden yükler. Arızanın
                //      ölçülen son hâli tam olarak buydu.
                //   3. Açılacak dosya bulunamasa bile pencere boşalmamalı.
                e.preventDefault();
                // `yerelYolaCevir`: href yüzde-kodlu geliyor (ölçüldü), ham
                // hâliyle gönderilse dosya diskte bulunamazdı.
                if (tur === 'yerel-dosya') onOpenFile?.(yerelYolaCevir(String(href)));
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
        ...(mentionTitles ? {
          p({ children, node: _node, ...props }: any) {
            return <p {...props}>{withMentionChips(children, mentionTitle)}</p>;
          },
          li({ children, node: _node, ...props }: any) {
            return <li {...props}>{withMentionChips(children, mentionTitle)}</li>;
          },
        } : {}),
        code({ inline, className, children, ...props }: any) {
          const match = /language-(\w+)/.exec(className || "");
          const codeString = String(children).replace(/\n$/, "");

          return !inline && match ? (
            <CodeBlock 
              match={match} 
              codeString={codeString} 
              workspacePath={workspacePath} 
              onExportToUnity={onExportToUnity}
              handleCopy={handleCopy}
              copiedBlock={copiedBlock}
              {...props}
            />
          ) : (
            <code className="bg-blue-500/10 px-1.5 py-0.5 rounded text-blue-400 font-mono text-xs font-semibold" {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
};

export const MarkdownRenderer = memo(MarkdownRendererInner);
