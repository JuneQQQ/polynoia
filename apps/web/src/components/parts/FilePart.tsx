/** FilePart — Doubao-style file card. Two actions:
 *   - 「打开预览」: route to the right-rail preview pane (only when this is
 *     a workspace-hosted file we can preview locally).
 *   - 「下载」: trigger a save dialog via /files/download (binary-faithful) for
 *     workspace files, or fall back to a plain <a download> for data URLs.
 *
 * The card stays in chat after preview closes — clicking 「打开预览」 reopens it.
 * For Agent-generated files the server emits src as
 * `/api/workspaces/<ws>/files/download?path=<path>` so we extract ws_id + path
 * and route the preview through the store.
 */
import {
	Download,
	FileCode2,
	FileImage,
	FileSpreadsheet,
	FileText,
	FileType,
	FileType2,
	type LucideIcon,
	Presentation,
} from "lucide-react";
import { api } from "../../lib/api";
import type { FilePayload } from "../../lib/types";
import { useStore } from "../../store";

function formatBytes(n: number | null | undefined): string {
	if (!n || n <= 0) return "";
	const units = ["B", "KB", "MB", "GB"];
	let i = 0;
	let v = n;
	while (v >= 1024 && i < units.length - 1) {
		v /= 1024;
		i++;
	}
	return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

/** Parse `/api/workspaces/<ws>/files/download?path=<path>` → `{wsId, path}`.
 * Returns null for anything else (data URL, external URL, attachment upload). */
function parseWorkspaceFileSrc(
	src: string,
): { wsId: string; path: string } | null {
	if (!src.startsWith("/api/workspaces/")) return null;
	try {
		const u = new URL(src, "http://_/");
		const m = u.pathname.match(
			/^\/api\/workspaces\/([^/]+)\/files\/download$/,
		);
		const path = u.searchParams.get("path");
		if (!m || !path) return null;
		return { wsId: m[1], path };
	} catch {
		return null;
	}
}

type Variant = {
	Icon: LucideIcon;
	bg: string;
	fg: string;
	label: string;
};

const TYPE_VARIANTS: Record<string, Variant> = {
	pptx: { Icon: Presentation, bg: "#FBE3D6", fg: "#D24726", label: "PPT" },
	docx: { Icon: FileType2, bg: "#DDE7F7", fg: "#2B579A", label: "Word" },
	xlsx: { Icon: FileSpreadsheet, bg: "#DBE9D9", fg: "#217346", label: "Excel" },
	pdf: { Icon: FileType, bg: "#F8D4D4", fg: "#D93025", label: "PDF" },
	html: { Icon: FileCode2, bg: "#FEEAD1", fg: "#E07A24", label: "HTML" },
	md: { Icon: FileText, bg: "#E3E6EE", fg: "#374151", label: "MD" },
	csv: { Icon: FileSpreadsheet, bg: "#E1EEDA", fg: "#34823F", label: "CSV" },
	tsv: { Icon: FileSpreadsheet, bg: "#E1EEDA", fg: "#34823F", label: "TSV" },
	png: { Icon: FileImage, bg: "#E8E1F2", fg: "#5E3FBE", label: "PNG" },
	jpg: { Icon: FileImage, bg: "#E8E1F2", fg: "#5E3FBE", label: "JPG" },
	jpeg: { Icon: FileImage, bg: "#E8E1F2", fg: "#5E3FBE", label: "JPG" },
};

function variantFor(name: string): Variant {
	const ext = name.toLowerCase().split(".").pop() ?? "";
	return (
		TYPE_VARIANTS[ext] ?? {
			Icon: FileText,
			bg: "var(--color-accent-soft)",
			fg: "var(--color-accent)",
			label: ext.toUpperCase() || "FILE",
		}
	);
}

export function FilePart({ payload }: { payload: FilePayload }) {
	const openCenterFile = useStore((s) => s.openCenterFile);

	const wsFile = parseWorkspaceFileSrc(payload.src);
	const size = formatBytes(payload.size_bytes);
	const { Icon, bg, fg, label } = variantFor(payload.name);

	const onPreview = () => {
		if (!wsFile) return;
		// Open the file CENTERED (a center editor/preview tab), same as the file
		// tree — not the right rail. Align the center-tab workspace to this file's
		// workspace first (CenterTabs reads preview.data.workspaceId), then open.
		useStore.setState((s) => ({
			preview: {
				...s.preview,
				data: { ...s.preview.data, workspaceId: wsFile.wsId },
			},
		}));
		openCenterFile(wsFile.path);
	};

	const onDownload = () => {
		if (wsFile) {
			api.downloadWorkspaceFile(wsFile.wsId, wsFile.path);
			return;
		}
		// Data URL / external — same-tab download via transient <a>.
		const a = document.createElement("a");
		a.href = payload.src;
		a.download = payload.name;
		a.target = "_blank";
		a.rel = "noopener noreferrer";
		document.body.appendChild(a);
		a.click();
		a.remove();
	};

	return (
		<div
			className="flex items-center gap-3 w-full max-w-[520px] p-2.5 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] hover:border-[var(--color-accent)] transition group"
			role="group"
			aria-label={`文件 ${payload.name}`}
		>
			<button
				type="button"
				onClick={wsFile ? onPreview : undefined}
				disabled={!wsFile}
				className={`flex items-center gap-2.5 min-w-0 flex-1 text-left ${wsFile ? "cursor-pointer" : "cursor-default"}`}
				title={wsFile ? "点击打开预览" : payload.name}
			>
				<div
					className="w-9 h-9 rounded-lg grid place-items-center flex-shrink-0 relative"
					style={{ background: bg, color: fg }}
				>
					<Icon size={16} />
					<span
						className="absolute -bottom-1 -right-1 px-1 py-px rounded text-[8px] font-bold leading-none"
						style={{ background: fg, color: "white" }}
					>
						{label}
					</span>
				</div>
				<div className="flex-1 min-w-0">
					<div className="text-[13px] font-medium text-[var(--color-fg)] truncate leading-snug">
						{payload.name}
					</div>
					<div className="text-[11px] text-[var(--color-fg-3)] truncate mt-0.5 font-mono">
						{[payload.media_type, size].filter(Boolean).join(" · ") || "文件"}
					</div>
				</div>
			</button>
			{/* Only a download button — the explicit "打开预览" button was removed at
			    the user's request (binary preview was buggy). Clicking the card
			    itself still opens the centered preview for renderable types. */}
			<div className="flex items-center gap-1 flex-shrink-0">
				<button
					type="button"
					onClick={onDownload}
					className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[var(--color-fg-2)] text-[11.5px] font-medium hover:bg-[var(--color-line)] transition"
				>
					<Download size={11} />
					下载
				</button>
			</div>
		</div>
	);
}
