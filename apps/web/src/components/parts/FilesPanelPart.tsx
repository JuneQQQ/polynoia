/** FilesPanelPart — a single deliverable panel bundling several files an agent
 * presented in ONE `present` call (the orchestrator's hand-off). Renders a
 * one-line `message` header + a list of compact file rows, each clickable to
 * preview in the right rail or download. One panel, not one card per file.
 *
 * Reuses FilePart's helpers (parseWorkspaceFileSrc / variantFor / formatBytes)
 * so the icon + preview/download routing stay identical to the single-file card.
 */
import { Download } from "lucide-react";
import { api } from "../../lib/api";
import type { FilesPayload } from "../../lib/types";
import { useStore } from "../../store";
import { formatBytes, parseWorkspaceFileSrc, variantFor } from "./FilePart";

function FileRow({
	src,
	name,
	sizeBytes,
}: {
	src: string;
	name: string;
	sizeBytes?: number | null;
}) {
	const openPreviewFile = useStore((s) => s.openPreviewFile);
	const wsFile = parseWorkspaceFileSrc(src);
	const { Icon, bg, fg, label } = variantFor(name);
	const size = formatBytes(sizeBytes);

	const onPreview = () => {
		if (!wsFile) return;
		// Mirror FilePart: align the preview workspace first, then open the file in
		// the right rail (mutually exclusive with the file tree there).
		useStore.setState((s) => ({
			preview: {
				...s.preview,
				data: { ...s.preview.data, workspaceId: wsFile.wsId },
			},
		}));
		openPreviewFile(wsFile.path);
	};

	const onDownload = () => {
		if (wsFile) {
			api.downloadWorkspaceFile(wsFile.wsId, wsFile.path);
			return;
		}
		const a = document.createElement("a");
		a.href = src;
		a.download = name;
		a.target = "_blank";
		a.rel = "noopener noreferrer";
		document.body.appendChild(a);
		a.click();
		a.remove();
	};

	return (
		<div className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition group">
			<button
				type="button"
				onClick={wsFile ? onPreview : undefined}
				disabled={!wsFile}
				className={`flex items-center gap-2.5 min-w-0 flex-1 text-left ${wsFile ? "cursor-pointer" : "cursor-default"}`}
				title={wsFile ? "点击打开预览" : name}
			>
				<div
					className="w-8 h-8 rounded-lg grid place-items-center flex-shrink-0 relative"
					style={{ background: bg, color: fg }}
				>
					<Icon size={15} />
					<span
						className="absolute -bottom-1 -right-1 px-1 py-px rounded text-[7px] font-bold leading-none"
						style={{ background: fg, color: "white" }}
					>
						{label}
					</span>
				</div>
				<div className="flex-1 min-w-0">
					<div className="text-[12.5px] font-medium text-[var(--color-fg)] truncate leading-snug">
						{name}
					</div>
					{size && (
						<div className="text-[10.5px] text-[var(--color-fg-3)] truncate mt-0.5 font-mono">
							{size}
						</div>
					)}
				</div>
			</button>
			<button
				type="button"
				onClick={onDownload}
				className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[var(--color-fg-2)] text-[11px] font-medium hover:bg-[var(--color-line)] transition flex-shrink-0"
			>
				<Download size={11} />
				下载
			</button>
		</div>
	);
}

export function FilesPanelPart({ payload }: { payload: FilesPayload }) {
	const { message, files } = payload;
	return (
		<div className="w-full max-w-[520px] rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] overflow-hidden">
			{message && (
				<div className="px-3 py-2 text-[13px] text-[var(--color-fg)] leading-relaxed border-b border-[var(--color-line)]">
					{message}
				</div>
			)}
			<div className="flex flex-col p-1">
				{files.map((f, i) => (
					<FileRow
						key={`${f.name}-${i}`}
						src={f.src}
						name={f.name}
						sizeBytes={f.size_bytes}
					/>
				))}
			</div>
		</div>
	);
}
