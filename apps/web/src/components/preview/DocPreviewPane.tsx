/** DocPreviewPane — renders a focused file by type, with export:
 *   - .md (document)      → CrepeEditor (WYSIWYG, editable, saves back)
 *   - Marp .md / .marp    → MarpPreview (slides; edit source in the code editor)
 *   - .csv / .tsv         → SheetPreview (Excel-style table)
 *   - .html               → sandboxed iframe (static)
 *
 * Driven by props (path + current content) so it slots into CodeEditor's
 * 预览 toggle in the center tabs — the code editor holds the source, this shows
 * the rich rendered view. `docKind` is exported so the editor knows when to
 * offer the toggle.
 */
import { useEffect, useState } from "react";
import { CrepeEditor } from "./CrepeEditor";
import { HtmlPreview } from "./HtmlPreview";
import { MarpPreview } from "./MarpPreview";
import { SheetPreview } from "./SheetPreview";

function isMarp(content: string): boolean {
	return (
		/^---/.test(content.trimStart()) &&
		/\bmarp:\s*true\b/.test(content.slice(0, 600))
	);
}

export type DocKind = "doc" | "marp" | "html" | "sheet" | "other";

export function docKind(path: string, content: string): DocKind {
	if (/\.html?$/i.test(path)) return "html";
	if (/\.(csv|tsv)$/i.test(path)) return "sheet";
	if (/\.marp$/i.test(path)) return "marp";
	if (/\.(md|markdown|mdx)$/i.test(path))
		return isMarp(content) ? "marp" : "doc";
	return "other";
}

function basename(path: string): string {
	return path.split("/").pop() ?? path;
}

export function DocPreviewPane({
	workspaceId,
	path,
	content,
}: {
	workspaceId: string | null;
	path: string;
	content: string;
}) {
	// Debounce for the live-rendered previews (Marp/HTML) so source edits don't
	// re-render every keystroke. Crepe owns its own state (no debounce).
	const [debounced, setDebounced] = useState(content);
	useEffect(() => {
		const t = window.setTimeout(() => setDebounced(content), 250);
		return () => window.clearTimeout(t);
	}, [content]);

	const kind = docKind(path, content);
	const name = basename(path);

	if (kind === "doc") {
		return workspaceId ? (
			<CrepeEditor
				key={path}
				workspaceId={workspaceId}
				path={path}
				content={content}
			/>
		) : (
			<Empty text="文档编辑需要在项目对话(workspace)里。" />
		);
	}
	if (kind === "sheet") {
		return <SheetPreview content={content} fileName={name} />;
	}
	if (kind === "marp") {
		return <MarpPreview content={debounced} fileName={name} />;
	}
	if (kind === "html") {
		return <HtmlPreview content={debounced} fileName={name} />;
	}
	return (
		<Empty
			text={`「${path}」无可视化预览。支持 .md(文档)、Marp(.md 带 marp:true 或 .marp)、.csv/.tsv(表格)、.html。`}
		/>
	);
}

function Empty({ text }: { text: string }) {
	return (
		<div className="h-full grid place-items-center bg-[var(--color-surface-2)]">
			<div className="text-center px-8 text-[12px] text-[var(--color-fg-3)] max-w-[340px] leading-relaxed">
				{text}
			</div>
		</div>
	);
}
