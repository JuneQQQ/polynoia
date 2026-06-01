/** MarpPreview — render a Marp markdown deck to slides + export.
 *
 * Source-edit + live-preview: the user edits the Marp markdown in the code tab
 * (CodeMirror); this renders slides and updates as they type (parent debounces
 * `content`). Export → PDF (browser print of the rendered deck). Read-only.
 */
import { Marp } from "@marp-team/marp-core";
import { Download } from "lucide-react";
import pptxgen from "pptxgenjs";
import { useCallback, useMemo } from "react";
import { downloadBlob, printAsPdf } from "./exportUtils";

/** Split a Marp deck into slides (strip front-matter, split on `---` lines),
 *  pulling a title + body text per slide — for the .pptx text export. */
function marpToSlides(content: string): { title: string; body: string }[] {
	let body = content;
	const fm = body.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
	if (fm) body = body.slice(fm[0].length);
	return body
		.split(/^---\s*$/m)
		.map((p) => p.trim())
		.filter(Boolean)
		.map((p) => {
			const lines = p.split("\n");
			const titleLine = lines.find((l) => /^#{1,3}\s/.test(l));
			const title = titleLine ? titleLine.replace(/^#{1,3}\s*/, "").trim() : "";
			const bodyText = lines
				.filter((l) => l !== titleLine)
				.join("\n")
				.trim();
			return { title, body: bodyText };
		});
}

// One instance, reused across renders (render() is pure per-call).
const marp = new Marp({ html: true });

function escapeHtml(s: string): string {
	return s.replace(
		/[<>&]/g,
		(c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[c] ?? c,
	);
}

export function MarpPreview({
	content,
	fileName,
}: { content: string; fileName?: string }) {
	const { html, css, error } = useMemo(() => {
		try {
			const r = marp.render(content);
			return { html: r.html, css: r.css, error: null as string | null };
		} catch (e) {
			return { html: "", css: "", error: String(e) };
		}
	}, [content]);

	const srcDoc = useMemo(() => {
		if (error) {
			return `<pre style="color:#b00020;padding:12px;white-space:pre-wrap;font:12px ui-monospace,monospace">幻灯渲染失败:\n${escapeHtml(error)}</pre>`;
		}
		return `<!doctype html><html><head><meta charset="utf-8"><style>${css}
      html,body{margin:0;padding:0;background:#f2f2f2}
    </style></head><body>${html}</body></html>`;
	}, [html, css, error]);

	const base = (fileName ?? "slides").replace(/\.[^.]+$/, "");

	const exportPptx = useCallback(async () => {
		const pptx = new pptxgen();
		pptx.defineLayout({ name: "W16x9", width: 10, height: 5.63 });
		pptx.layout = "W16x9";
		for (const s of marpToSlides(content)) {
			const slide = pptx.addSlide();
			if (s.title) {
				slide.addText(s.title, {
					x: 0.5,
					y: 0.3,
					w: 9,
					h: 0.9,
					fontSize: 28,
					bold: true,
				});
			}
			if (s.body) {
				slide.addText(s.body, {
					x: 0.5,
					y: 1.4,
					w: 9,
					h: 3.8,
					fontSize: 16,
					valign: "top",
				});
			}
		}
		const blob = (await pptx.write({ outputType: "blob" })) as Blob;
		downloadBlob(blob, `${base}.pptx`);
	}, [content, base]);

	return (
		<div className="h-full flex flex-col bg-[#f2f2f2]">
			<div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
				<span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">
					{fileName ?? "幻灯"}
				</span>
				<button
					type="button"
					onClick={() =>
						!error && printAsPdf(html, base, `<style>${css}</style>`)
					}
					disabled={!!error}
					className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition disabled:opacity-40 flex-shrink-0"
				>
					<Download size={11} /> PDF
				</button>
				<button
					type="button"
					onClick={exportPptx}
					title="导出 PowerPoint (.pptx,纯文本,不含 Marp 主题样式)"
					className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
				>
					.pptx
				</button>
			</div>
			<iframe
				title="marp-preview"
				sandbox="allow-scripts"
				srcDoc={srcDoc}
				className="flex-1 w-full border-0 bg-[#f2f2f2]"
			/>
		</div>
	);
}
