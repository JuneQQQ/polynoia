/** ProjectRunPane — run the WHOLE workspace project in a Docker container and
 * iframe its live page (the real project, not a single file). Detects the type
 * → starts the container via the runner API → polls until the port answers →
 * shows localhost:{port}. Shows install/build logs + stop/restart. Docker
 * isolation collapses host-runtime deps into "host needs Docker". See ADR-018.
 */
import { AlertTriangle, Loader2, Play, RotateCcw, Square, Terminal } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type RunStatus } from "../../lib/api";

const KIND_LABEL: Record<string, string> = {
  static: "静态站点",
  npm: "前端工程",
  python: "Python 服务",
  unknown: "未识别",
};

export function ProjectRunPane({ workspaceId }: { workspaceId: string | null }) {
  const [run, setRun] = useState<RunStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState("");
  const [showLogs, setShowLogs] = useState(false);
  const pollRef = useRef<number | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pullLogs = useCallback(() => {
    if (workspaceId) api.runLogs(workspaceId).then((r) => setLogs(r.logs)).catch(() => {});
  }, [workspaceId]);

  const startPoll = useCallback(() => {
    if (!workspaceId) return;
    stopPoll();
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await api.getRunStatus(workspaceId);
        setRun(s);
        pullLogs();
        if (s.status === "running" || s.status === "error" || s.status === "stopped") {
          stopPoll();
        }
      } catch {
        /* transient — keep polling */
      }
    }, 1500);
  }, [workspaceId, stopPoll, pullLogs]);

  // On open: fetch current status (it may already be running from before).
  useEffect(() => {
    if (!workspaceId) {
      setRun(null);
      return;
    }
    let alive = true;
    api
      .getRunStatus(workspaceId)
      .then((s) => {
        if (!alive) return;
        setRun(s);
        if (s.status === "starting") startPoll();
      })
      .catch(() => {});
    return () => {
      alive = false;
      stopPoll();
    };
  }, [workspaceId, startPoll, stopPoll]);

  const start = async () => {
    if (!workspaceId || busy) return;
    setBusy(true);
    setLogs("");
    try {
      const s = await api.runProject(workspaceId);
      setRun(s);
      if (s.status === "starting") startPoll();
      else pullLogs();
    } catch (e) {
      setRun({ ws_id: workspaceId, kind: "", status: "error", error: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!workspaceId || busy) return;
    setBusy(true);
    stopPoll();
    try {
      await api.stopProject(workspaceId);
      setRun({ ws_id: workspaceId, kind: "", status: "stopped" });
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };

  const status = run?.status ?? "stopped";
  const kindLabel = run?.kind ? (KIND_LABEL[run.kind] ?? run.kind) : "";
  const active = status === "running" || status === "starting";

  return (
    <div className="h-full flex flex-col bg-white">
      {/* toolbar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
        <Play size={12} style={{ color: "var(--color-green)" }} className="flex-shrink-0" />
        <span className="font-medium text-[var(--color-fg-2)]">整个项目</span>
        {kindLabel && (
          <span
            className="text-[9.5px] px-1.5 py-0.5 rounded font-mono uppercase tracking-wide"
            style={{ background: "var(--color-line)", color: "var(--color-fg-3)" }}
          >
            {kindLabel}
          </span>
        )}
        <span className="flex-1" />
        {active && (
          <button
            type="button"
            onClick={stop}
            disabled={busy}
            title="停止"
            aria-label="停止"
            className="p-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-red)] hover:bg-[var(--color-line)] disabled:opacity-40"
          >
            <Square size={12} />
          </button>
        )}
        {status === "running" && (
          <button
            type="button"
            onClick={() => {
              stop().then(start);
            }}
            disabled={busy}
            title="重启"
            aria-label="重启"
            className="p-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-40"
          >
            <RotateCcw size={12} />
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            setShowLogs((v) => !v);
            pullLogs();
          }}
          title="日志"
          aria-label="日志"
          aria-pressed={showLogs}
          className={`p-1 rounded hover:bg-[var(--color-line)] ${showLogs ? "text-[var(--color-accent)]" : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"}`}
        >
          <Terminal size={12} />
        </button>
      </div>

      {/* body */}
      <div className="flex-1 min-h-0 relative">
        {!workspaceId ? (
          <Empty text="本对话不在项目里。在项目对话里,可以把整个项目跑起来预览。" />
        ) : status === "running" && run?.url ? (
          <iframe
            key={run.url}
            title="project-preview"
            src={run.url}
            className="absolute inset-0 w-full h-full border-0 bg-white"
          />
        ) : status === "starting" ? (
          <Center>
            <Loader2 size={16} className="animate-spin" />
            <div className="mt-2">
              启动中…{" "}
              <span className="text-[var(--color-fg-4)]">检测 / 装依赖 / 起容器</span>
            </div>
          </Center>
        ) : status === "error" ? (
          <div className="absolute inset-0 overflow-auto p-3">
            <div
              className="flex items-center gap-1.5 text-[11px] font-medium mb-2"
              style={{ color: "var(--color-red)" }}
            >
              <AlertTriangle size={13} /> 跑不起来
            </div>
            <pre className="text-[11.5px] leading-[1.55] mono whitespace-pre-wrap text-[var(--color-red)]">
              {run?.error || "未知错误"}
            </pre>
            <button
              type="button"
              onClick={start}
              disabled={busy}
              className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-[11.5px] rounded font-medium text-white hover:opacity-90 disabled:opacity-50"
              style={{ background: "var(--color-green)" }}
            >
              <RotateCcw size={12} /> 重试
            </button>
          </div>
        ) : (
          <Center>
            <button
              type="button"
              onClick={start}
              disabled={busy}
              className="inline-flex items-center gap-2 px-4 py-2 text-[13px] rounded-lg font-medium text-white hover:opacity-90 disabled:opacity-50"
              style={{ background: "var(--color-green)" }}
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} 运行整个项目
            </button>
            <div className="mt-2 text-[11px] text-[var(--color-fg-4)]">
              在 Docker 容器里跑起来,点开直接看页面
            </div>
          </Center>
        )}
      </div>

      {/* logs drawer */}
      {showLogs && (
        <div className="h-40 border-t border-[var(--color-line)] bg-[var(--color-code-bg)] overflow-auto flex-shrink-0">
          <pre className="px-3 py-2 mono text-[11px] leading-[1.5] whitespace-pre-wrap text-[var(--color-code-fg)]">
            {logs || "(暂无日志)"}
          </pre>
        </div>
      )}
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-0 grid place-items-center text-[12px] text-[var(--color-fg-3)] text-center px-8">
      <div>{children}</div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="absolute inset-0 grid place-items-center bg-[var(--color-surface-2)]">
      <div className="text-center px-8 text-[12px] text-[var(--color-fg-3)] max-w-[320px] leading-relaxed">
        {text}
      </div>
    </div>
  );
}
