/** LivePreviewPane — run the focused file and show it live (Artifacts-style).
 *
 * Multi-adapter by file type (a renderer registry — open for Vue/Svelte/etc.):
 *  - .html / .htm        → rendered straight into a sandboxed <iframe>
 *  - .jsx/.tsx/.js/.ts    → React, compiled in-browser via lazy @babel/standalone
 *
 * Reads `openCodeFile` (mirrored from CodeTab, unsaved edits included) and
 * re-renders ~300ms after edits, so typing (or an agent writing the file)
 * updates the preview live. React/ReactDOM come from local /vendor UMD bundles
 * (NOT a CDN) so it works offline / on-prem.
 *
 * First-version scope (React): single-file component with `export default`,
 * only `react` / `react-dom` imports resolve. Vue SFC, external deps, multi-file
 * imports, and BACKEND live-run (separate track) come later.
 */
import { AlertTriangle, Loader2, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../../store";

type RendererKind = "html" | "react";

function rendererKind(path: string): RendererKind | null {
  if (/\.html?$/i.test(path)) return "html";
  if (/\.(jsx|tsx|js|ts)$/i.test(path)) return "react";
  return null;
}

/** HTML adapter — the file IS the document. Sandboxed; scripts allowed so inline
 * <script> runs. (Relative asset refs won't resolve in a srcdoc — single-file /
 * inlined HTML for now; multi-file support is a later step.) */
function buildHtmlSrcDoc(html: string): string {
  return html;
}

/** React adapter — wrap compiled CommonJS in a sandbox doc: load React UMD from
 * /vendor, provide a tiny require() mapping react/react-dom to the globals,
 * mount the default export, and surface runtime errors instead of a blank page. */
function buildReactSrcDoc(compiled: string, origin: string): string {
  return `<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;background:#fff;font-family:system-ui,sans-serif}
  #__err{position:fixed;inset:0;margin:0;padding:12px;white-space:pre-wrap;
    font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;color:#b00020;
    background:#fff;overflow:auto;z-index:99999}
</style>
<script src="${origin}/vendor/react.production.min.js"></script>
<script src="${origin}/vendor/react-dom.production.min.js"></script>
</head><body><div id="__root"></div>
<script>
(function(){
  function showErr(m){var e=document.getElementById('__err')||document.createElement('pre');
    e.id='__err';e.textContent=String(m);document.body.appendChild(e);}
  window.addEventListener('error',function(ev){showErr((ev.error&&ev.error.stack)||ev.message||'运行错误');});
  window.addEventListener('unhandledrejection',function(ev){showErr('Promise 未捕获: '+((ev.reason&&ev.reason.stack)||ev.reason));});
  try{
    if(!window.React||!window.ReactDOM){showErr('React 运行时未加载(/vendor 不可访问?)');return;}
    var exports={};var module={exports:exports};
    function require(m){
      if(m==='react')return window.React;
      if(m==='react-dom'||m==='react-dom/client')return window.ReactDOM;
      throw new Error('第一版预览暂不支持外部依赖: "'+m+'"(目前只支持 react / react-dom)');
    }
    (function(exports,module,require){
${compiled}
    })(exports,module,require);
    var C=(exports&&exports.default)||(module.exports&&module.exports.default)||module.exports;
    if(typeof C!=='function'&&!(C&&typeof C==='object')){
      showErr('没找到要渲染的组件 —— 请用 "export default" 导出一个 React 组件。');return;}
    window.ReactDOM.createRoot(document.getElementById('__root')).render(window.React.createElement(C));
  }catch(err){showErr((err&&err.stack)||err);}
})();
</script></body></html>`;
}

export function LivePreviewPane() {
  const file = useStore((s) => s.openCodeFile);
  const [srcDoc, setSrcDoc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [nonce, setNonce] = useState(0); // bump → force iframe remount (re-run)
  const babelRef = useRef<typeof import("@babel/standalone") | null>(null);

  // Load the compiler (~2.5MB) lazily, and ONLY when a React file needs it.
  const ensureBabel = useCallback(async () => {
    if (!babelRef.current) babelRef.current = await import("@babel/standalone");
    return babelRef.current;
  }, []);

  const kind = file ? rendererKind(file.path) : null;
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  // Debounced (re)render — edits settle ~300ms, then rebuild the iframe doc.
  useEffect(() => {
    if (!file || !kind) {
      setSrcDoc(null);
      setError(null);
      return;
    }
    let alive = true;
    setCompiling(true);
    const t = window.setTimeout(async () => {
      try {
        let doc: string;
        if (kind === "html") {
          doc = buildHtmlSrcDoc(file.content);
        } else {
          const Babel = await ensureBabel();
          const out = Babel.transform(file.content, {
            filename: file.path,
            presets: [
              ["react", { runtime: "classic" }],
              ["typescript", { isTSX: true, allExtensions: true, allowDeclareFields: true }],
            ],
            plugins: ["transform-modules-commonjs"],
            sourceType: "module",
          });
          doc = buildReactSrcDoc(out.code ?? "", origin);
        }
        if (alive) {
          setSrcDoc(doc);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setCompiling(false);
      }
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [file, kind, origin, ensureBabel]);

  return (
    <div className="h-full flex flex-col bg-white">
      {/* toolbar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
        <Play size={12} style={{ color: "var(--color-green)" }} className="flex-shrink-0" />
        <span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">
          {file?.path ?? "未打开文件"}
        </span>
        {kind && (
          <span
            className="text-[9.5px] px-1.5 py-0.5 rounded font-mono uppercase tracking-wide flex-shrink-0"
            style={{ background: "var(--color-line)", color: "var(--color-fg-3)" }}
          >
            {kind}
          </span>
        )}
        {compiling && <Loader2 size={11} className="animate-spin text-[var(--color-fg-3)]" />}
        <button
          type="button"
          onClick={() => setNonce((n) => n + 1)}
          disabled={!srcDoc}
          title="重新运行"
          aria-label="重新运行"
          className="p-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-40"
        >
          <RotateCcw size={12} />
        </button>
      </div>

      {/* body */}
      <div className="flex-1 min-h-0 relative">
        {error ? (
          <div className="absolute inset-0 overflow-auto p-3">
            <div
              className="flex items-center gap-1.5 text-[11px] font-medium mb-2"
              style={{ color: "var(--color-red)" }}
            >
              <AlertTriangle size={13} /> 编译错误
            </div>
            <pre className="text-[11.5px] leading-[1.55] mono whitespace-pre-wrap text-[var(--color-red)]">
              {error}
            </pre>
          </div>
        ) : !file ? (
          <Empty text="在左侧代码区打开一个文件,这里实时渲染它的效果。支持 .html 网页、.jsx / .tsx React 组件。" />
        ) : !kind ? (
          <Empty
            text={`「${file.path}」不是可预览的类型。第一版支持 .html / .htm 网页,以及 .jsx / .tsx / .js / .ts(默认导出一个 React 组件)。`}
          />
        ) : srcDoc === null ? (
          <div className="absolute inset-0 grid place-items-center text-[12px] text-[var(--color-fg-3)]">
            <span className="inline-flex items-center gap-1.5">
              <Loader2 size={13} className="animate-spin" /> 渲染中…
            </span>
          </div>
        ) : (
          <iframe
            key={nonce}
            title="live-preview"
            sandbox="allow-scripts"
            srcDoc={srcDoc}
            className="absolute inset-0 w-full h-full border-0 bg-white"
          />
        )}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="absolute inset-0 grid place-items-center bg-[var(--color-surface-2)]">
      <div className="text-center px-8 text-[12px] text-[var(--color-fg-3)] max-w-[340px] leading-relaxed">
        {text}
      </div>
    </div>
  );
}
