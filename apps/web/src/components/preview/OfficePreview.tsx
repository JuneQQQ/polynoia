/** OfficePreview — render a BINARY office file (.docx/.xlsx/.pptx) in the center
 * lane from raw bytes.
 *
 * Unlike DocPreviewPane (which is text/string-driven), these formats are
 * ZIP/OOXML binaries, so we fetch bytes via api.workspaceFileBytes (the
 * byte-faithful /files/download endpoint) and render entirely client-side:
 *   - .xlsx/.xls → SheetJS (already a dep): every sheet as a tab + table
 *   - .docx      → docx-preview (renderAsync → Word-like styled DOM)
 *   - .pptx      → pptx-preview (init → preview(bytes) → rendered slides)
 *
 * The two heavy renderers (docx/pptx) are loaded via dynamic import() so a
 * missing/broken lib degrades to a clean "download to view" card instead of
 * taking down the whole pane. Preview-only: binary files have no meaningful
 * UTF-8 source, so CodeEditor hides the 源码 toggle for these kinds.
 */
import { Download, FileWarning, Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as XLSX from "xlsx";
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

	if (kind === "xlsx")
		return <XlsxView buf={buf} name={name} onDownload={download} />;
	if (kind === "word")
		return <DocxView buf={buf} name={name} onDownload={download} />;
	if (kind === "slides")
		return <PptxView buf={buf} name={name} onDownload={download} />;
	return (
		<Fallback name={name} reason="不支持的 Office 类型" onDownload={download} />
	);
}

/* ---------- XLSX (SheetJS, sync) ---------- */

const SHEET_CSS = `
.office-xlsx table{border-collapse:collapse;font:12.5px system-ui,sans-serif;color:#111}
.office-xlsx th,.office-xlsx td{border:1px solid #d4d4d8;padding:4px 10px;text-align:left;vertical-align:top;background:#fff}
.office-xlsx thead th{background:#f3f4f6;font-weight:600;position:sticky;top:0;z-index:1}
`;

function XlsxView({
	buf,
	name,
	onDownload,
}: {
	buf: ArrayBuffer;
	name: string;
	onDownload: () => void;
}) {
	const { sheets, error } = useMemo(() => {
		try {
			const wb = XLSX.read(buf, { type: "array" });
			const sheets = wb.SheetNames.map((sn) => {
				const ws = wb.Sheets[sn];
				const html = ws
					? XLSX.utils.sheet_to_html(ws, { editable: false })
					: "";
				return { name: sn, html };
			});
			return { sheets, error: null as string | null };
		} catch (e) {
			return { sheets: [], error: e instanceof Error ? e.message : String(e) };
		}
	}, [buf]);
	const [active, setActive] = useState(0);

	if (error)
		return <Fallback name={name} reason={error} onDownload={onDownload} />;
	if (sheets.length === 0)
		return <Fallback name={name} reason="工作簿为空" onDownload={onDownload} />;

	const cur = sheets[Math.min(active, sheets.length - 1)];
	return (
		<div className="h-full flex flex-col bg-[var(--color-surface-2)]">
			<style>{SHEET_CSS}</style>
			{sheets.length > 1 && (
				<div className="flex items-center gap-0.5 px-2 py-1 border-b border-[var(--color-line)] bg-[var(--color-surface)] overflow-x-auto">
					{sheets.map((s, i) => (
						<button
							key={s.name}
							type="button"
							onClick={() => setActive(i)}
							className={`px-2 py-0.5 rounded text-[11px] font-mono whitespace-nowrap ${
								i === active
									? "bg-[var(--color-accent)] text-white"
									: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
							}`}
							title={s.name}
						>
							{s.name}
						</button>
					))}
				</div>
			)}
			<div className="flex-1 overflow-auto p-3 office-xlsx">
				{/* biome-ignore lint/security/noDangerouslySetInnerHtml: SheetJS sheet_to_html escapes cell content (its own implementation); we sandwich it in our own <table> CSS. */}
				<div dangerouslySetInnerHTML={{ __html: cur.html }} />
			</div>
		</div>
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
	const ref = useRef<HTMLDivElement | null>(null);
	const [err, setErr] = useState<string | null>(null);

	useEffect(() => {
		let alive = true;
		setErr(null);
		import("pptx-preview")
			.then((mod) => {
				if (!alive || !ref.current) return;
				ref.current.innerHTML = "";
				// pptx-preview API: init(container, opts).preview(bytes) → Promise
				type PptxPreviewModule = {
					init: (
						container: HTMLElement,
						opts?: { width?: number; height?: number },
					) => { preview: (b: ArrayBuffer) => Promise<unknown> };
				};
				const m = mod as unknown as PptxPreviewModule;
				const previewer = m.init(ref.current, { width: 960, height: 540 });
				return previewer.preview(buf);
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
		<div className="h-full overflow-auto bg-[var(--color-surface-2)] p-3">
			<div ref={ref} className="pptx-preview mx-auto" />
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
