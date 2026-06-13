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
import {
	Download,
	FileWarning,
	LayoutGrid,
	Loader2,
	StretchHorizontal,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { t } from "../../lib/i18n";
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
	const lang = useStore((s) => s.lang);
	// Re-fetch when an agent rewrites files on main (same trigger CodeEditor uses).
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const name = basename(path);
	const download = () => api.downloadWorkspaceFile(workspaceId, path);

	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger — re-fetch bytes when an agent rewrites this file on main.
	useEffect(() => {
		let alive = true;
		setBuf(null);
		setError(null);
		// Fetch via /files/blob (in-memory Response) rather than /files/download
		// (streamed FileResponse): the latter fails over the Vite proxy from a
		// remote device ("Failed to fetch"), while blob works. 25MB cap is fine
		// for office docs.
		api
			.workspaceFileBytesRead(workspaceId, path)
			.then((r) => r.data)
			.then((b) => {
				if (!alive) return;
				if (!isZip(b)) {
					setError(t("invalidOfficeFormat", lang));
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
		<Fallback
			name={name}
			reason={t("unsupportedOfficeType", lang)}
			onDownload={download}
		/>
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
			<div ref={ref} className="docx-preview text-[#111]" />
			{/* Fit-to-pane overrides for docx-preview's defaults. Its wrapper centers
			    a FIXED-width page (align-items:center + inline page width); on a pane
			    narrower than the page that pushes the page's LEFT edge off-screen
			    (unreachable by scroll — the "left characters clipped" bug) and shows a
			    gray bar up top. Left-align + drop the gray bg + force the page to the
			    pane width so text reflows to fit instead of overflowing. Classes are
			    docx-preview's own (wrapper = `${className}-wrapper`, page = section.
			    `${className}`, with className="docx-preview-doc" from renderAsync). */}
			<style>{`
				.docx-preview .docx-preview-doc-wrapper {
					background: transparent !important;
					padding: 0 !important;
					align-items: stretch !important;
				}
				.docx-preview .docx-preview-doc-wrapper > section.docx-preview-doc {
					width: 100% !important;
					min-width: 0 !important;
					box-shadow: none !important;
					margin: 0 !important;
					padding: 28px 32px !important;
				}
				.docx-preview .docx-preview-doc-wrapper > section.docx-preview-doc img,
				.docx-preview .docx-preview-doc-wrapper > section.docx-preview-doc table {
					max-width: 100% !important;
					height: auto;
				}
			`}</style>
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
	// pptx-preview is a SINGLE-slide paginated viewer (renders only the current
	// slide + a pager). To show the whole deck we drive its internal
	// htmlRender.renderSlide(i) for every index into its one wrapper (they
	// accumulate since we never removeCurrentSlide), then CSS them into one of two
	// layouts. "scroll" = full-width slides stacked one per row (read each in
	// detail); "grid" = small thumbnails many-per-row (overview at a glance).
	const [mode, setMode] = useState<"scroll" | "grid">("scroll");
	const [paneW, setPaneW] = useState<number | null>(null);
	const lang = useStore((s) => s.lang);

	// ResizeObserver, debounced 200ms (a width change re-parses the whole deck).
	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		let timer: number | null = null;
		const measure = () => setPaneW(el.clientWidth);
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

	// Slide render width by mode: large for scroll (readable), small for grid.
	const width = useMemo(() => {
		if (!paneW) return null;
		const avail = Math.max(280, paneW - 24);
		return mode === "grid" ? Math.min(avail, 300) : Math.min(avail, 1000);
	}, [paneW, mode]);

	useEffect(() => {
		if (!width || !ref.current) return;
		let alive = true;
		setErr(null);
		import("pptx-preview")
			.then(async (mod) => {
				if (!alive || !ref.current) return;
				ref.current.innerHTML = "";
				// biome-ignore lint/suspicious/noExplicitAny: pptx-preview ships no types; we reach its internal renderSlide to show ALL slides.
				const pv: any = (mod as any).init(ref.current, {
					width,
					height: Math.round((width * 9) / 16),
				});
				await pv.preview(buf);
				if (!alive) return;
				// preview() renders only slide 0 + a pager. Render the rest into the
				// same wrapper so every slide stacks. Guarded: if the lib internals
				// change we degrade to the single-slide view rather than crashing.
				const n: number = pv.slideCount ?? 0;
				const render = pv.htmlRender?.renderSlide?.bind(pv.htmlRender);
				if (render) for (let i = 1; i < n; i++) render(i);
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
			style={{ padding: 12 }}
		>
			{/* Layout toggle: 逐页平铺 (continuous) vs 网格总览 (overview). */}
			<div className="sticky top-0 z-10 mb-3 flex w-fit gap-1 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] p-0.5 shadow-sm">
				{(
					[
						["scroll", StretchHorizontal, t("slideScrollMode", lang)],
						["grid", LayoutGrid, t("slideGridMode", lang)],
					] as const
				).map(([m, Icon, label]) => (
					<button
						key={m}
						type="button"
						onClick={() => setMode(m)}
						aria-pressed={mode === m}
						title={label}
						className={`grid h-7 w-7 place-items-center rounded-md transition ${mode === m ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]" : "text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"}`}
					>
						<Icon size={15} />
					</button>
				))}
			</div>
			<div
				ref={ref}
				className={`pptx-all ${mode === "grid" ? "pptx-grid" : "pptx-scroll"}`}
			/>
			{/* Show EVERY slide (not the lib's 1-slide pager): force slide-wrappers
			    relative, lay them out per mode, hide the pager/next chrome. A CSS
			    counter stamps each slide's page number, slide-sorter style. */}
			<style>{`
				.pptx-all .pptx-preview-wrapper {
					height: auto !important;
					width: auto !important;
					overflow: visible !important;
					counter-reset: pptx-slide;
					display: flex;
					justify-content: center;
				}
				.pptx-all.pptx-scroll .pptx-preview-wrapper { flex-direction: column; align-items: center; gap: 22px; }
				.pptx-all.pptx-grid .pptx-preview-wrapper { flex-wrap: wrap; align-content: flex-start; gap: 14px; }
				.pptx-all .pptx-preview-wrapper-pagination,
				.pptx-all .pptx-preview-wrapper-next { display: none !important; }
				.pptx-all .pptx-preview-slide-wrapper {
					position: relative !important;
					top: auto !important;
					left: auto !important;
					margin: 0 !important;
					border-radius: 6px;
					box-shadow: 0 0 0 1px var(--color-line), var(--shadow-card);
				}
				.pptx-all .pptx-preview-slide-wrapper::after {
					counter-increment: pptx-slide;
					content: counter(pptx-slide);
					position: absolute;
					bottom: 6px;
					right: 8px;
					font-size: 11px;
					color: var(--color-fg-3);
					background: var(--color-surface);
					border: 1px solid var(--color-line);
					border-radius: 4px;
					padding: 0 6px;
					pointer-events: none;
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
	const lang = useStore((s) => s.lang);
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
					<Download size={12} /> {t("downloadToView", lang)}
				</button>
			</div>
		</div>
	);
}
