/** RightPreviewFile — what the right rail renders when previewing a file.
 *
 * Thin wrapper over DocPreviewPane (which self-routes by docKind):
 *   - .md / .marp / .html → fetch UTF-8 text here, pass as `content`
 *   - .xlsx ("workbook")  → DocPreviewPane fetches its own bytes (WorkbookPreview),
 *     so we skip the text fetch (it would 415) and pass content=""
 *   - .pptx ("slides")    → rendered INLINE here (PptxRender) with the right-rail's
 *     own layout rules (width-fit, 8px safe inset, vertical scroll). Bypasses
 *     OfficePreview's PptxView so this component owns the pptx layout fully.
 *   - anything else       → DocPreviewPane shows an "no preview / download" card
 *
 * Re-fetches when an agent rewrites the file (workspaceFilesTick).
 */
import { Download, FileX2, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { isMobile } from "../../lib/platform";
import { assetUrl } from "../../lib/runtime-config";
import { useStore } from "../../store";
import { DocPreviewPane, docKind } from "./DocPreviewPane";
import { MobileMarkdownView } from "./MobileMarkdownView";

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

	// Byte-based kinds — DocPreviewPane fetches their bytes itself (xlsx→Workbook,
	// docx/pptx→Office). Recognizable by extension, so skip the text fetch (415).
	// .pptx renders via the dedicated PptxRender below (width-fit, vertical scroll).
	const _k = docKind(path, "");
	const isSlides = _k === "slides";
	const isBinary = _k === "workbook" || _k === "word";
	// Mobile renders images directly (<img>) and never needs the text fetch for
	// them — the raw text endpoint would 415 on binary image bytes.
	const isImage = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i.test(path);
	const mobile = isMobile();
	const skipImageFetch = isImage && mobile;

	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is the reload trigger.
	useEffect(() => {
		// Skip text fetch for byte-based kinds (their previewers fetch bytes
		// themselves). MUST stay after all hook calls — early-returning before
		// this effect when isSlides flipped (file switch from .py to .pptx)
		// caused "rendered fewer hooks than expected".
		if (isBinary || isSlides || skipImageFetch) return;
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
	}, [workspaceId, path, filesTick, isBinary, isSlides, skipImageFetch]);

	// Routing — order matters: pptx + binary docs render WITHOUT a text fetch
	// (their previewers fetch bytes themselves), so route them before the
	// loading/content checks below.
	if (isSlides) {
		return <PptxRender workspaceId={workspaceId} path={path} />;
	}
	if (isBinary) {
		// DocPreviewPane → WorkbookPreview fetches the .xlsx bytes itself.
		return <DocPreviewPane workspaceId={workspaceId} path={path} content="" />;
	}
	// Mobile image preview — render the bytes directly via <img> (same-origin
	// /files/blob through the Vite proxy; assetUrl honors any server override).
	if (mobile && isImage) {
		const src = assetUrl(
			`/api/workspaces/${encodeURIComponent(workspaceId)}/files/blob?path=${encodeURIComponent(path)}`,
		);
		return (
			<div className="h-full w-full overflow-auto bg-[var(--color-surface-2)] grid place-items-center p-3">
				{/* biome-ignore lint/a11y/useAltText: filename alt below */}
				<img src={src} alt={path} className="max-w-full h-auto object-contain" />
			</div>
		);
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
	// Mobile: robust read-only path that avoids the heavy/editable desktop
	// renderers (CrepeEditor/Milkdown, CodeMirror SourcePreview) which can crash
	// or fail to lazy-load in the Android WebView. Every text file shows its
	// content; desktop keeps the rich renderers untouched.
	if (mobile) {
		// Markdown (incl. Marp source) → lightweight react-markdown.
		if (/\.(md|markdown|mdx|marp)$/i.test(path)) {
			return <MobileMarkdownView content={content} />;
		}
		// csv/tsv (table) + html (rendered) — these DocPreviewPane renderers are
		// light and work fine in the WebView.
		if (/\.(csv|tsv|html?)$/i.test(path)) {
			return (
				<DocPreviewPane workspaceId={workspaceId} path={path} content={content} />
			);
		}
		// Everything else that's UTF-8 text (code / json / yaml / txt / logs / …)
		// → show the raw content. Binary-unknown files fail the text fetch above
		// and land on ErrorCard instead.
		return (
			<pre className="h-full w-full overflow-auto bg-[var(--color-surface-2)] m-0 p-3 text-[12px] leading-relaxed font-mono whitespace-pre text-[var(--color-fg-2)]">
				{content}
			</pre>
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

/** PptxRender — right-rail-owned pptx renderer. Width-fit + vertical scroll.
 *
 * Layout rules (locked by spec, never auto-change):
 *   - Outer container: w=100%, h=100%, 8px safe inset L/R, `overflow-y-auto`,
 *     `overflow-x-hidden` (slides never crop sideways, just scroll down).
 *   - Each slide width = container_width − 16. Height = width × 9/16 (16:9).
 *   - ResizeObserver + 200ms debounce → re-init pptx-preview at new width
 *     when the user drags the pane. pptx-preview re-parses on each init,
 *     debounce avoids thrashing during the drag.
 *   - Bypasses DocPreviewPane / OfficePreview so this is the single owner
 *     of the pptx layout — no double containers, no nested overflow.
 */
function PptxRender({
	workspaceId,
	path,
}: { workspaceId: string; path: string }) {
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const containerRef = useRef<HTMLDivElement | null>(null);
	const slidesRef = useRef<HTMLDivElement | null>(null);
	const [buf, setBuf] = useState<ArrayBuffer | null>(null);
	const [err, setErr] = useState<string | null>(null);
	const [width, setWidth] = useState<number | null>(null);

	// Fetch .pptx bytes (re-fetch when agent rewrites file → filesTick bumps).
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is reload trigger.
	useEffect(() => {
		let alive = true;
		setBuf(null);
		setErr(null);
		api
			.workspaceFileBytes(workspaceId, path)
			.then((b) => {
				if (alive) setBuf(b);
			})
			.catch((e) => {
				if (alive) setErr(String(e?.message ?? e));
			});
		return () => {
			alive = false;
		};
	}, [workspaceId, path, filesTick]);

	// Measure container width with the spec's 8px L/R safe inset budget.
	// Debounced so dragging the pane handle only re-renders once at the end.
	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const PAD = 8; // each side; 16px total horizontal budget per spec
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

	// Render (or re-render on width change). pptx-preview is loaded lazily so a
	// missing/broken lib only kills this pane, not the whole right rail.
	useEffect(() => {
		if (!buf || !width) return;
		let alive = true;
		setErr(null);
		import("pptx-preview")
			.then((mod) => {
				if (!alive || !slidesRef.current) return;
				slidesRef.current.innerHTML = "";
				type PptxPreviewModule = {
					init: (
						container: HTMLElement,
						opts?: { width?: number; height?: number },
					) => { preview: (b: ArrayBuffer) => Promise<unknown> };
				};
				const m = mod as unknown as PptxPreviewModule;
				const p = m.init(slidesRef.current, {
					width,
					height: Math.round((width * 9) / 16),
				});
				return p.preview(buf);
			})
			.catch((e) => {
				if (alive) setErr(String(e?.message ?? e));
			});
		return () => {
			alive = false;
		};
	}, [buf, width]);

	return (
		<div
			ref={containerRef}
			className="h-full w-full overflow-y-auto overflow-x-hidden bg-[var(--color-surface-2)]"
			style={{
				paddingLeft: 8,
				paddingRight: 8,
				paddingTop: 8,
				paddingBottom: 8,
			}}
		>
			{err ? (
				<div className="h-full grid place-items-center">
					<div className="text-center max-w-[320px]">
						<FileX2 size={28} className="text-[var(--color-fg-4)] mx-auto mb-3" />
						<div className="text-[13px] font-medium text-[var(--color-fg)] mb-1 truncate">
							{basename(path)}
						</div>
						<div className="text-[11px] text-[var(--color-fg-3)] mb-3">
							无法预览:{err}
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
			) : !buf ? (
				<div className="h-full grid place-items-center text-[12px] text-[var(--color-fg-3)]">
					<Loader2 size={14} className="animate-spin" />
				</div>
			) : (
				<div ref={slidesRef} className="rp-pptx" />
			)}
			{/* pptx-preview defaults work against us in a right-rail context:
			    when its init() gets a `height`, the lib's own
			    `.pptx-preview-wrapper` becomes a fixed-height SCROLL BOX with a
			    black background — so all slides scroll INSIDE the lib's box and
			    only the first one is visible in our pane. These overrides return
			    the wrapper to natural flow so slides stack normally and our
			    OUTER container is the single scroll context. */}
			<style>{`
				.rp-pptx .pptx-preview-wrapper {
					height: auto !important;
					overflow: visible !important;
					background: transparent !important;
					margin: 0 !important;
					width: 100% !important;
				}
			`}</style>
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
