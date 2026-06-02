/** RightPreviewFile — what the right rail renders when in "预览" mode.
 *
 * Routes a workspace file path to the correct renderer:
 *   - .docx/.xlsx/.pptx → OfficePreview (binary, fetches bytes)
 *   - .md/.marp/.csv/.tsv/.html → DocPreviewPane (text, fetched as UTF-8)
 *   - anything else → fallback card with download button
 *
 * Both branches re-fetch when an agent rewrites the file (workspaceFilesTick).
 */
import { Download, FileX2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import { DocPreviewPane, docKind, isBinaryDocKind } from "./DocPreviewPane";
import { OfficePreview } from "./OfficePreview";

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
	// Text-based previews need the file content as UTF-8 — fetch here once and
	// pass to DocPreviewPane. Binary previews go straight to OfficePreview which
	// owns its own ArrayBuffer fetch.
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const [content, setContent] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// docKind needs content to disambiguate .md (marp or plain doc), but the
	// binary trio is recognizable from the extension alone — short-circuit so
	// we don't waste a text fetch that would 415 anyway.
	const kindHint = docKind(path, "");
	const isBinary = isBinaryDocKind(kindHint);

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
		return <OfficePreview workspaceId={workspaceId} path={path} kind={kindHint} />;
	}
	if (loading || content === null) {
		if (error) return <ErrorCard path={path} workspaceId={workspaceId} reason={error} />;
		return (
			<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
				<Loader2 size={14} className="animate-spin" />
			</div>
		);
	}
	return <DocPreviewPane workspaceId={workspaceId} path={path} content={content} />;
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
				<div className="text-[13px] text-[var(--color-fg-2)] mb-1.5">暂无预览</div>
				<div className="text-[11px] text-[var(--color-fg-3)] leading-relaxed">
					Agent 生成文件后,会自动出现在聊天里;点击文件卡片的「打开预览」即可在此查看。
				</div>
			</div>
		</div>
	);
}
