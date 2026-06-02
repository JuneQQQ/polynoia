/** OfficePreview — render a BINARY office doc (.docx / .pptx) from raw bytes.
 * (.xlsx is handled separately by the editable WorkbookPreview.)
 *
 * These are ZIP/OOXML binaries, so we fetch bytes via api.workspaceFileBytes
 * (the byte-faithful /files/download endpoint) and render client-side:
 *   - .docx → docx-preview (renderAsync → Word-like styled DOM)
 *   - .pptx → pptx-preview (init → preview(bytes) → rendered slides)
 *
 * Both renderers are loaded via dynamic import() so a missing/broken lib
 * degrades to a clean "download to view" card instead of taking down the
 * whole pane. Preview-only — binary files have no meaningful UTF-8 source.
 */
import { Download, FileWarning, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import type { DocKind } from "./DocPreviewPane";

function basename(path: string): string {
	return path.split("/").pop() ?? path;
}

/** ZIP / OOXML magic bytes: "PK\x03\x04" (some empty/streamed archives use
 * PK\x05\x06 / PK\x07\x08; we accept all three to be safe). */
function isZip(buf: ArrayBuffer): boolean {
	if (buf.byteLength < 4) return false;
	const v = new Uint8Array(buf, 0, 4);
	if (v[0] !== 0x50 || v[1] !== 0x4b) return false;
	return (
		(v[2] === 0x03 && v[3] === 0x04) ||
		(v[2] === 0x05 && v[3] === 0x06) ||
		(v[2] === 0x07 && v[3] === 0x08)
	);
}

export function OfficePreview({
	workspaceId,
	path,
	kind,
}: {
	workspaceId: string;
	path: string;
	kind: DocKind;
}) {
	const [buf, setBuf] = useState<ArrayBuffer | null>(null);
	const [error, setError] = useState<string | null>(null);
	// Re-fetch when an agent rewrites files on main (same trigger CodeEditor uses).
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const name = basename(path);
	const download = () => api.downloadWorkspaceFile(workspaceId, path);

	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger — re-fetch bytes when an agent rewrites this file on main.
	useEffect(() => {
		let alive = true;
		setBuf(null);
		setError(null);
		api
			.workspaceFileBytes(workspaceId, path)
			.then((b) => {
				if (!alive) return;
				if (!isZip(b)) {
					setError("文件不是有效的 OOXML(ZIP)结构,无法预览");
					return;
				}
				setBuf(b);
			})
			.catch((e) => {
				if (alive) setError(String(e));
			});
		return () => {
			alive = false;
		};
	}, [workspaceId, path, filesTick]);

	if (error)
		return <Fallback name={name} reason={error} onDownload={download} />;
	if (!buf) {
		return (
			<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
				<Loader2 size={14} className="animate-spin" />
			</div>
		);
	}

	if (kind === "word")
		return <DocxView buf={buf} name={name} onDownload={download} />;
	if (kind === "slides")
		return <PptxView buf={buf} name={name} onDownload={download} />;
	return (
		<Fallback name={name} reason="不支持的 Office 类型" onDownload={download} />
	);
}

/* ---------- DOCX (docx-preview, async) ---------- */

function DocxView({
	buf,
	name,
	onDownload,
}: {
	buf: ArrayBuffer;
	name: string;
	onDownload: () => void;
}) {
	const ref = useRef<HTMLDivElement | null>(null);
	const [err, setErr] = useState<string | null>(null);

	useEffect(() => {
		let alive = true;
		setErr(null);
		import("docx-preview")
			.then((mod) => {
				if (!alive || !ref.current) return;
				ref.current.innerHTML = "";
				return mod.renderAsync(buf, ref.current, undefined, {
					className: "docx-preview-doc",
					inWrapper: true,
				});
			})
			.catch((e) => {
				if (alive) setErr(String(e));
			});
		return () => {
			alive = false;
		};
	}, [buf]);

	if (err) return <Fallback name={name} reason={err} onDownload={onDownload} />;
	return (
		<div className="h-full overflow-auto bg-white">
			<div ref={ref} className="docx-preview p-4 text-[#111]" />
		</div>
	);
}

/* ---------- PPTX (pptx-preview, async) ---------- */

function PptxView({
	buf,
	name,
	onDownload,
}: {
	buf: ArrayBuffer;
	name: string;
	onDownload: () => void;
}) {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const ref = useRef<HTMLDivElement | null>(null);
	const [err, setErr] = useState<string | null>(null);
	// WIDTH-FIT — slide width fills the available pane width minus 12px
	// padding on each side. Slide height = width × 9/16 (16:9 aspect). Never
	// crops horizontally; slides stack vertically and the outer container
	// gives a vertical scrollbar when total height exceeds the pane.
	const [width, setWidth] = useState<number | null>(null);

	// ResizeObserver, debounced 200ms so dragging the pane handle only re-
	// renders once at the end (pptx-preview re-parses the whole deck on init).
	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const PAD = 12; // each side; 24px horizontal budget total
		const MIN = 280; // below this slides become unreadable
		let timer: number | null = null;
		const measure = () => {
			const w = Math.max(MIN, el.clientWidth - PAD * 2);
			setWidth(w);
		};
		measure();
		const ro = new ResizeObserver(() => {
			if (timer !== null) window.clearTimeout(timer);
			timer = window.setTimeout(measure, 200);
		});
		ro.observe(el);
		return () => {
			ro.disconnect();
			if (timer !== null) window.clearTimeout(timer);
		};
	}, []);

	useEffect(() => {
		if (!width) return;
		let alive = true;
		setErr(null);
		import("pptx-preview")
			.then((mod) => {
				if (!alive || !ref.current) return;
				ref.current.innerHTML = "";
				type PptxPreviewModule = {
					init: (
						container: HTMLElement,
						opts?: { width?: number; height?: number },
					) => { preview: (b: ArrayBuffer) => Promise<unknown> };
				};
				const m = mod as unknown as PptxPreviewModule;
				const previewer = m.init(ref.current, {
					width,
					height: Math.round((width * 9) / 16),
				});
				return previewer.preview(buf);
			})
			.catch((e) => {
				if (alive) setErr(String(e));
			});
		return () => {
			alive = false;
		};
	}, [buf, width]);

	if (err) return <Fallback name={name} reason={err} onDownload={onDownload} />;
	return (
		<div
			ref={containerRef}
			className="h-full w-full overflow-y-auto overflow-x-hidden bg-[var(--color-surface-2)]"
			style={{ paddingLeft: 12, paddingRight: 12, paddingTop: 12, paddingBottom: 12 }}
		>
			<div ref={ref} className="pptx-preview pptx-preview-fit" />
			{/* Inter-slide spacing only — no horizontal/vertical stretching of slides. */}
			<style>{`
				.pptx-preview-fit > * {
					margin-bottom: 12px;
				}
				.pptx-preview-fit > *:last-child {
					margin-bottom: 0;
				}
			`}</style>
		</div>
	);
}

/* ---------- Fallback (lib missing / bad bytes / unsupported) ---------- */

function Fallback({
	name,
	reason,
	onDownload,
}: {
	name: string;
	reason: string;
	onDownload: () => void;
}) {
	return (
		<div className="grid place-items-center h-full bg-[var(--color-surface-2)]">
			<div className="flex flex-col items-center gap-2 p-6 max-w-[360px] text-center">
				<FileWarning size={28} className="text-[var(--color-fg-3)]" />
				<div className="text-[12.5px] font-mono text-[var(--color-fg-2)] truncate max-w-full">
					{name}
				</div>
				<div className="text-[11px] text-[var(--color-fg-3)] leading-relaxed">
					{reason}
				</div>
				<button
					type="button"
					onClick={onDownload}
					className="inline-flex items-center gap-1.5 px-3 py-1.5 mt-1 rounded bg-[var(--color-accent)] text-white text-[11.5px] font-medium hover:opacity-90"
				>
					<Download size={12} /> 下载查看
				</button>
			</div>
		</div>
	);
}
