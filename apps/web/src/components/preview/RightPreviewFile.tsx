/** RightPreviewFile — what the right rail renders when previewing a file.
 *
 * Thin wrapper over DocPreviewPane (which self-routes by docKind):
 *   - .md / .marp / .html → fetch UTF-8 text here, pass as `content`
 *   - .xlsx ("workbook")  → DocPreviewPane fetches its own bytes (WorkbookPreview),
 *     so we skip the text fetch (it would 415) and pass content=""
 *   - anything else       → DocPreviewPane shows an "no preview / download" card
 *
 * Re-fetches when an agent rewrites the file (workspaceFilesTick).
 */
import { Download, FileX2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import { DocPreviewPane, docKind } from "./DocPreviewPane";

function basename(path: string): string {
	return path.split("/").pop() ?? path;
}

export function RightPreviewFile({
	workspaceId,
	path,
}: {
	workspaceId: string;
	path: string;
}) {
	// Text previews (doc/marp/html) need UTF-8 content — fetch once here and pass
	// to DocPreviewPane. The .xlsx "workbook" kind is byte-based: DocPreviewPane →
	// WorkbookPreview fetches its own ArrayBuffer, so we skip the text fetch.
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const [content, setContent] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// "workbook" (.xlsx) is the only kind DocPreviewPane fetches bytes for itself
	// — recognizable from the extension alone, so skip the text fetch (would 415).
	const isBinary = docKind(path, "") === "workbook";

	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is the reload trigger.
	useEffect(() => {
		if (isBinary) return;
		let alive = true;
		setContent(null);
		setError(null);
		setLoading(true);
		api
			.workspaceFileRead(workspaceId, path)
			.then((res) => {
				if (alive) setContent(res.content);
			})
			.catch((e) => {
				if (alive) setError(String(e?.message ?? e));
			})
			.finally(() => {
				if (alive) setLoading(false);
			});
		return () => {
			alive = false;
		};
	}, [workspaceId, path, filesTick, isBinary]);

	if (isBinary) {
		// DocPreviewPane → WorkbookPreview fetches the .xlsx bytes itself.
		return <DocPreviewPane workspaceId={workspaceId} path={path} content="" />;
	}
	if (loading || content === null) {
		if (error)
			return <ErrorCard path={path} workspaceId={workspaceId} reason={error} />;
		return (
			<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
				<Loader2 size={14} className="animate-spin" />
			</div>
		);
	}
	return (
		<DocPreviewPane workspaceId={workspaceId} path={path} content={content} />
	);
}

function ErrorCard({
	path,
	workspaceId,
	reason,
}: { path: string; workspaceId: string; reason: string }) {
	return (
		<div className="h-full grid place-items-center bg-[var(--color-surface-2)] px-6">
			<div className="text-center max-w-[320px]">
				<FileX2 size={28} className="text-[var(--color-fg-4)] mx-auto mb-3" />
				<div className="text-[13px] font-medium text-[var(--color-fg)] mb-1 truncate">
					{basename(path)}
				</div>
				<div className="text-[11px] text-[var(--color-fg-3)] mb-3">
					无法预览:{reason}
				</div>
				<button
					type="button"
					onClick={() => api.downloadWorkspaceFile(workspaceId, path)}
					className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-[var(--color-accent)] text-white text-[12px] hover:opacity-90"
				>
					<Download size={12} /> 下载
				</button>
			</div>
		</div>
	);
}

export function RightPreviewEmpty() {
	return (
		<div className="h-full grid place-items-center bg-[var(--color-surface-2)] px-6">
			<div className="text-center max-w-[280px]">
				<div className="text-[13px] text-[var(--color-fg-2)] mb-1.5">
					暂无预览
				</div>
				<div className="text-[11px] text-[var(--color-fg-3)] leading-relaxed">
					Agent
					生成文件后,会自动出现在聊天里;点击文件卡片的「打开预览」即可在此查看。
				</div>
			</div>
		</div>
	);
}
