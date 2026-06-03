/** DocPreviewPane — renders a focused file by type, with export:
 *   - .md (document)      → CrepeEditor (WYSIWYG, editable, saves back)
 *   - Marp .md / .marp    → MarpPreview (slides; edit source in the code editor)
 *   - .xlsx               → WorkbookPreview (editable binary workbook)
 *   - .csv / .tsv         → SheetPreview (read-only table view)
 *   - .html               → sandboxed iframe (static)
 *
 * Driven by props (path + current content) so it slots into CodeEditor's
 * 预览 toggle in the center tabs — the code editor holds the source, this shows
 * the rich rendered view. `docKind` is exported so the editor knows when to
 * offer the toggle.
 */
import { Suspense, lazy, useEffect, useState } from "react";
import { HtmlPreview } from "./HtmlPreview";
import { OfficePreview } from "./OfficePreview";
import { PreviewErrorBoundary } from "./PreviewErrorBoundary";
import { SheetPreview } from "./SheetPreview";
import { SourcePreview } from "./SourcePreview";

// Lazy: heavy renderers not needed at boot — CrepeEditor (Milkdown, ~80KB),
// MarpPreview (Marp core, ~50KB), WorkbookPreview (xlsx, ~40KB). They load only
// when a .md / .marp / .xlsx file is actually previewed.
const CrepeEditor = lazy(() =>
	import("./CrepeEditor").then((m) => ({ default: m.CrepeEditor })),
);
const MarpPreview = lazy(() =>
	import("./MarpPreview").then((m) => ({ default: m.MarpPreview })),
);
const WorkbookPreview = lazy(() =>
	import("./WorkbookPreview").then((m) => ({ default: m.WorkbookPreview })),
);

const _DocFallback = (
	<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
		加载中…
	</div>
);

/** Source-code extensions that the right rail can preview-edit via
 * CodeMirror. Aligned with CodeEditor's langExtForPath: anything that has
 * syntax highlighting is recognized as code (.py first per user ask, then the
 * common companions). Excludes .md/.html — they have richer rendered kinds. */
const CODE_EXT = /\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|swift|c|h|cc|cpp|hpp|rb|php|sh|bash|zsh|json|yaml|yml|toml|css|scss|less|sql|xml|svg)$/i;

/** Download URL for a workspace file (fallback when binary preview fails). */
function downloadHref(workspaceId: string | null, path: string): string | undefined {
	if (!workspaceId) return undefined;
	return `/api/workspaces/${encodeURIComponent(workspaceId)}/files/download?path=${encodeURIComponent(path)}`;
}

function isMarp(content: string): boolean {
	return (
		/^---/.test(content.trimStart()) &&
		/\bmarp:\s*true\b/.test(content.slice(0, 600))
	);
}

export type DocKind =
	| "doc"
	| "marp"
	| "html"
	| "workbook"
	| "sheet"
	| "word"
	| "slides"
	| "code"
	| "other";

export function docKind(path: string, content: string): DocKind {
	if (/\.html?$/i.test(path)) return "html";
	if (/\.xlsx$/i.test(path)) return "workbook";
	if (/\.(csv|tsv)$/i.test(path)) return "sheet";
	if (/\.docx$/i.test(path)) return "word";
	if (/\.pptx$/i.test(path)) return "slides";
	if (/\.marp$/i.test(path)) return "marp";
	if (/\.(md|markdown|mdx)$/i.test(path))
		return isMarp(content) ? "marp" : "doc";
	if (CODE_EXT.test(path)) return "code";
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
			<Suspense fallback={_DocFallback}>
				<CrepeEditor
					key={path}
					workspaceId={workspaceId}
					path={path}
					content={content}
				/>
			</Suspense>
		) : (
			<Empty text="文档编辑需要在项目对话(workspace)里。" />
		);
	}
	if (kind === "workbook") {
		return workspaceId ? (
			<PreviewErrorBoundary
				downloadHref={downloadHref(workspaceId, path)}
				fileName={name}
			>
				<Suspense fallback={_DocFallback}>
					<WorkbookPreview
						workspaceId={workspaceId}
						path={path}
						fileName={name}
					/>
				</Suspense>
			</PreviewErrorBoundary>
		) : (
			<Empty text="表格编辑需要在项目对话(workspace)里。" />
		);
	}
	if (kind === "sheet") {
		return <SheetPreview content={content} fileName={name} />;
	}
	if (kind === "word" || kind === "slides") {
		return workspaceId ? (
			<PreviewErrorBoundary
				downloadHref={downloadHref(workspaceId, path)}
				fileName={name}
			>
				<OfficePreview workspaceId={workspaceId} path={path} kind={kind} />
			</PreviewErrorBoundary>
		) : (
			<Empty text="Office 文档预览需要在项目对话(workspace)里。" />
		);
	}
	if (kind === "marp") {
		return (
			<Suspense fallback={_DocFallback}>
				<MarpPreview content={debounced} fileName={name} />
			</Suspense>
		);
	}
	if (kind === "html") {
		return <HtmlPreview content={debounced} fileName={name} />;
	}
	if (kind === "code") {
		return workspaceId ? (
			<SourcePreview
				key={path}
				workspaceId={workspaceId}
				path={path}
				content={content}
			/>
		) : (
			<Empty text="源码预览需要在项目对话(workspace)里。" />
		);
	}
	return (
		<Empty
			text={`「${path}」无可视化预览。支持 .md(文档)、Marp(.md 带 marp:true 或 .marp)、.xlsx(可编辑表格)、.csv/.tsv(只读表格)、.docx/.pptx(Office)、.html、源码(.py/.ts/.js/...)。`}
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
