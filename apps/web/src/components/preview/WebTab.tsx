/** WebTab — preview workspace HTML files in an iframe.
 *
 * Phase B (P1.2): replaced the previous MOCK_LANDING_HTML with a real
 * file picker that lists `.html` files under the active workspace and
 * loads the chosen one via `/api/workspaces/{ws_id}/preview?file=...`.
 *
 * Only renders meaningfully when the active conv is workspace-shared
 * (`workspace_id` non-null). DMs show an empty state.
 */
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import type { WebPayload } from "../../lib/types";
import { useStore } from "../../store";

const DEVICES = [
  { id: "desktop", label: "桌面", w: 1440, h: 900 },
  { id: "tablet", label: "平板", w: 1024, h: 768 },
  { id: "mobile", label: "手机", w: 390, h: 844 },
] as const;

type FileEntry = { name: string; type: "file" | "dir"; size: number | null; modified: number };

export function WebTab({ payload }: { payload?: WebPayload | null }) {
  const [device, setDevice] = useState<(typeof DEVICES)[number]["id"]>("desktop");
  const activeConvId = useStore((s) => s.activeConvId);
  const convs = useStore((s) => s.convs);

  // We don't have direct ConvSummary here — walk the store for workspace_id.
  // ChatPane fetches it on convId change, but WebTab is sibling. Approximate
  // by holding the workspaceId in store under preview.data.workspaceId if
  // available; fall back to scanning known conv data.
  const previewData = useStore((s) => s.preview.data);
  const workspaceId = previewData?.workspaceId ?? null;

  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [picked, setPicked] = useState<string | null>(
    payload?.file_path ?? null,
  );
  const [refreshTick, setRefreshTick] = useState(0);

  // Load top-level .html files. We could recurse but root-level is the
  // 99% case (index.html, demo.html etc.) — deeper structures are rare.
  useEffect(() => {
    if (!workspaceId) {
      setFiles(null);
      return;
    }
    let alive = true;
    api.workspaceFiles(workspaceId, "")
      .then((res) => {
        if (!alive) return;
        const htmls = res.entries.filter(
          (e) => e.type === "file" && /\.(html?|htm)$/i.test(e.name),
        );
        setFiles(htmls);
        if (!picked && htmls.length > 0) {
          // Prefer index.html if present
          const idx = htmls.find((e) => e.name.toLowerCase() === "index.html");
          setPicked(idx ? idx.name : htmls[0].name);
        }
      })
      .catch(() => alive && setFiles([]));
    return () => { alive = false; };
  }, [workspaceId, refreshTick, picked]);

  const d = DEVICES.find((x) => x.id === device)!;
  const frameWidth = device === "desktop" ? "100%" : `${d.w * 0.7}px`;
  const frameSrc = useMemo(
    () => (workspaceId && picked ? api.workspacePreviewUrl(workspaceId, picked) : null),
    [workspaceId, picked],
  );

  if (!workspaceId) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-surface-2)]">
        <div className="flex-1 grid place-items-center text-[12px] text-[var(--color-fg-3)] px-8 text-center">
          <div>
            <div className="font-display text-[15px] text-[var(--color-fg-2)] mb-1">
              网页预览
            </div>
            <div>本对话不在项目里。在项目对话中,agent 生成的 HTML 文件会在这里预览。</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[var(--color-surface-2)]">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
        <div className="inline-flex gap-1 bg-[var(--color-surface-2)] p-0.5 rounded-md border border-[var(--color-line)]">
          {DEVICES.map((dv) => (
            <button
              key={dv.id}
              type="button"
              onClick={() => setDevice(dv.id)}
              className={`px-2.5 py-1 text-[11px] rounded transition ${
                device === dv.id
                  ? "bg-[var(--color-surface)] shadow-sm font-medium"
                  : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
              }`}
            >
              {dv.label}
            </button>
          ))}
        </div>
        <span className="text-[10.5px] mono text-[var(--color-fg-3)]">
          {d.w} × {d.h}
        </span>
        {files !== null && files.length > 0 && (
          <select
            value={picked ?? ""}
            onChange={(e) => setPicked(e.target.value)}
            className="text-[11.5px] font-mono px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-surface-2)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
            title="选择要预览的 HTML 文件"
          >
            {files.map((f) => (
              <option key={f.name} value={f.name}>{f.name}</option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={() => setRefreshTick((n) => n + 1)}
          className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
          title="刷新"
        >
          <RefreshCw size={11} />
        </button>
        <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] text-[var(--color-fg-3)]">
          {files === null
            ? <Loader2 size={10} className="animate-spin" />
            : <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-green)]" />}
          {files === null ? "加载中" : files.length === 0 ? "无 HTML 文件" : "已同步"}
        </span>
      </div>
      <div className="flex-1 overflow-auto p-4 grid place-items-center">
        {frameSrc ? (
          <div
            className="bg-[var(--color-surface)] border border-[var(--color-line)] rounded-lg overflow-hidden shadow-md transition-all"
            style={{ width: frameWidth, maxWidth: "100%", height: device === "desktop" ? "100%" : `${d.h * 0.7}px` }}
          >
            <iframe
              key={frameSrc + refreshTick}
              title={payload?.title ?? picked ?? "preview"}
              src={frameSrc}
              sandbox="allow-scripts allow-same-origin"
              referrerPolicy="no-referrer"
              className="w-full h-full border-0"
            />
          </div>
        ) : (
          <div className="text-[12px] text-[var(--color-fg-3)]">
            还没有可预览的 HTML 文件。让 agent 生成一个 <code className="font-mono">.html</code>。
          </div>
        )}
      </div>
    </div>
  );
}
