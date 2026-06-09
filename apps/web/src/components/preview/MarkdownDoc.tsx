/** MarkdownDoc — 文档级只读 Markdown 渲染(暖色主题,贴合 Polynoia 设计语言)。
 *
 * 用现成的 react-markdown(remark-gfm + rehype-highlight,已是依赖)做只读精排,
 * 替代重型可编辑的 CrepeEditor 作为 .md 的「默认预览」。可选 onEdit 渲染「编辑」
 * 按钮,切到 CrepeEditor 修改。桌面 DocPreviewPane + 移动 MobileMarkdownView 共用,
 * 三端同一套排版。
 *
 * 样式全部走 CSS 变量(--color-* / --font-*),自动适配深浅色;标题用衬线
 * (--font-display),链接/列表标记/引用条用余烬橙(--color-accent),代码块沿用全局
 * highlight.js 主题。错误边界兜底:渲染抛错时退化成纯文本源码,绝不空白。
 */
import { FileText, Pencil } from "lucide-react";
import {
	Component,
	type MouseEvent,
	type ReactNode,
	useMemo,
	useRef,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { assetUrl } from "../../lib/runtime-config";
import { fixCjkMarkdown } from "../parts/TextPart";

/** Resolve a markdown image `src` to something the browser can load.
 *
 * Agent reports embed images by RELATIVE path (`chart_category.png`,
 * `imgs/x.png`, `../a.png`) — those live in the same workspace as the .md, so we
 * rewrite them to the workspace blob endpoint (which now serves the real image
 * media-type). Absolute/external/data URLs are left untouched. Paths are
 * normalized against the doc's own directory so `../` works. */
function resolveDocImageSrc(
	src: string | undefined,
	workspaceId: string | undefined,
	docPath: string | undefined,
): string | undefined {
	if (!src || !workspaceId) return src;
	if (/^([a-z]+:|\/\/|\/|#)/i.test(src)) return src; // http(s)/data/blob/abs/anchor
	const docDir = docPath?.includes("/")
		? docPath.slice(0, docPath.lastIndexOf("/"))
		: "";
	const parts: string[] = [];
	for (const seg of `${docDir}/${src}`.split("/")) {
		if (seg === "" || seg === ".") continue;
		if (seg === "..") parts.pop();
		else parts.push(seg);
	}
	const norm = parts.join("/");
	return assetUrl(
		`/api/workspaces/${encodeURIComponent(workspaceId)}/files/blob?path=${encodeURIComponent(norm)}`,
	);
}

function textFromChildren(children: ReactNode): string {
	if (typeof children === "string" || typeof children === "number") {
		return String(children);
	}
	if (Array.isArray(children)) return children.map(textFromChildren).join("");
	if (
		children &&
		typeof children === "object" &&
		"props" in children &&
		(children as { props?: { children?: ReactNode } }).props
	) {
		return textFromChildren(
			(children as { props: { children?: ReactNode } }).props.children,
		);
	}
	return "";
}

export function githubHeadingSlug(text: string): string {
	return text
		.trim()
		.toLowerCase()
		.replace(/[^\p{L}\p{N}\s-]/gu, "")
		.replace(/\s+/g, "-")
		.replace(/-+/g, "-")
		.replace(/^-|-$/g, "");
}

function uniqueSlug(base: string, counts: Map<string, number>): string {
	const key = base || "section";
	const seen = counts.get(key) ?? 0;
	counts.set(key, seen + 1);
	return seen === 0 ? key : `${key}-${seen}`;
}

function buildHeadingIdMap(markdown: string): Map<number, string> {
	const out = new Map<number, string>();
	const counts = new Map<string, number>();
	let inFence = false;
	let offset = 0;
	for (const line of markdown.split("\n")) {
		if (/^\s*(```|~~~)/.test(line)) {
			inFence = !inFence;
			offset += line.length + 1;
			continue;
		}
		if (!inFence) {
			const h = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
			if (h) out.set(offset, uniqueSlug(githubHeadingSlug(h[2]), counts));
		}
		offset += line.length + 1;
	}
	return out;
}

function nodeStartOffset(node: unknown): number | null {
	const pos = (node as { position?: { start?: { offset?: unknown } } } | null)
		?.position;
	const offset = pos?.start?.offset;
	return typeof offset === "number" ? offset : null;
}

function findAnchorTarget(root: HTMLElement, raw: string): HTMLElement | null {
	const decoded = decodeURIComponent(raw).replace(/^#/, "");
	if (!decoded) return null;
	let el = root.querySelector<HTMLElement>(
		`[id="${decoded.replace(/["\\]/g, "\\$&")}"]`,
	);
	if (el) return el;
	const slug = githubHeadingSlug(decoded);
	el = root.querySelector<HTMLElement>(`[id="${slug.replace(/["\\]/g, "\\$&")}"]`);
	return el;
}

const DOC_STYLE = `
.pn-md-doc{font-family:var(--font-ui);color:var(--color-fg);font-size:15px;line-height:1.75;word-break:break-word}
.pn-md-doc>*:first-child{margin-top:0}
.pn-md-doc>*:last-child{margin-bottom:0}
.pn-md-doc h1,.pn-md-doc h2,.pn-md-doc h3,.pn-md-doc h4{font-family:var(--font-display);color:var(--color-fg);line-height:1.3;font-weight:600;margin:1.6em 0 .6em;letter-spacing:.01em}
.pn-md-doc h1{font-size:1.85em;margin-top:.1em;padding-bottom:.3em;border-bottom:1px solid var(--color-line)}
.pn-md-doc h2{font-size:1.42em;padding-bottom:.24em;border-bottom:1px solid var(--color-line)}
.pn-md-doc h3{font-size:1.2em}
.pn-md-doc h4{font-size:1.05em}
.pn-md-doc h5,.pn-md-doc h6{font-family:var(--font-ui);font-size:.95em;font-weight:650;color:var(--color-fg-2);margin:1.3em 0 .5em}
.pn-md-doc p{margin:.85em 0}
.pn-md-doc a{color:var(--color-accent);text-decoration:none;border-bottom:1px solid var(--color-accent-soft);transition:border-color .15s}
.pn-md-doc a:hover{border-bottom-color:var(--color-accent)}
.pn-md-doc strong{font-weight:680;color:var(--color-fg)}
.pn-md-doc em{font-style:italic}
.pn-md-doc ul,.pn-md-doc ol{margin:.85em 0;padding-left:1.5em}
.pn-md-doc li{margin:.32em 0}
.pn-md-doc li::marker{color:var(--color-accent-dim)}
.pn-md-doc ul ul,.pn-md-doc ol ol,.pn-md-doc ul ol,.pn-md-doc ol ul{margin:.3em 0}
.pn-md-doc ul.contains-task-list{list-style:none;padding-left:.2em}
.pn-md-doc li.task-list-item{list-style:none}
.pn-md-doc li.task-list-item input{margin-right:.5em;accent-color:var(--color-accent)}
.pn-md-doc blockquote{margin:1em 0;padding:.4em 1.1em;border-left:3px solid var(--color-accent);background:var(--color-surface-2);color:var(--color-fg-2);border-radius:0 6px 6px 0}
.pn-md-doc blockquote p{margin:.4em 0}
.pn-md-doc :not(pre)>code{font-family:var(--font-mono);font-size:.86em;background:var(--color-surface-3);color:var(--color-accent-dim);padding:.12em .42em;border-radius:4px;border:.5px solid var(--color-line)}
.pn-md-doc pre{margin:1em 0;padding:14px 16px;overflow-x:auto;background:var(--color-surface-2);border:1px solid var(--color-line);border-radius:8px;font-size:.86em;line-height:1.6}
.pn-md-doc pre code{font-family:var(--font-mono);background:none;border:none;padding:0;color:var(--color-fg);font-size:1em}
.pn-md-doc hr{border:none;border-top:1px solid var(--color-line);margin:2em 0}
.pn-md-doc table{border-collapse:collapse;margin:1em 0;display:block;width:max-content;max-width:100%;overflow-x:auto;font-size:.92em}
.pn-md-doc th,.pn-md-doc td{border:1px solid var(--color-line);padding:7px 13px;text-align:left}
.pn-md-doc thead th{background:var(--color-surface-3);font-weight:650}
.pn-md-doc tbody tr:nth-child(2n){background:var(--color-surface-2)}
.pn-md-doc img{max-width:100%;border-radius:8px;margin:.5em 0}
.pn-md-doc kbd{font-family:var(--font-mono);font-size:.82em;background:var(--color-surface-3);border:1px solid var(--color-line-2);border-bottom-width:2px;border-radius:4px;padding:.1em .4em}
`;

function RawText({ content }: { content: string }) {
	return (
		<pre className="h-full w-full overflow-auto m-0 p-4 text-[13px] leading-relaxed font-mono whitespace-pre-wrap text-[var(--color-fg)]">
			{content}
		</pre>
	);
}

/** Falls back to raw text if the markdown renderer throws in this WebView. */
class MdBoundary extends Component<
	{ content: string; children: ReactNode },
	{ failed: boolean }
> {
	state = { failed: false };
	static getDerivedStateFromError() {
		return { failed: true };
	}
	render() {
		if (this.state.failed) return <RawText content={this.props.content} />;
		return this.props.children;
	}
}

export function MarkdownDoc({
	content,
	path,
	workspaceId,
	imgBasePath,
	onEdit,
}: {
	content: string;
	path?: string;
	workspaceId?: string;
	/** Doc path used ONLY to resolve relative image src (decoupled from `path`,
	 *  which also drives the header — mobile passes this but no `path`). */
	imgBasePath?: string;
	onEdit?: () => void;
}) {
	const name = path ? (path.split("/").pop() ?? path) : undefined;
	const imgDocPath = imgBasePath ?? path;
	const scrollRef = useRef<HTMLDivElement>(null);
	const fixedContent = useMemo(() => fixCjkMarkdown(content), [content]);
	const headingIds = useMemo(() => buildHeadingIdMap(fixedContent), [fixedContent]);
	const scrollToHash = (href: string, event?: MouseEvent<HTMLAnchorElement>) => {
		if (!href.startsWith("#")) return false;
		const root = scrollRef.current;
		event?.preventDefault();
		if (!root) return true;
		const target = findAnchorTarget(root, href);
		if (!target) return true;
		target.scrollIntoView({ behavior: "smooth", block: "start" });
		return true;
	};
	// Recreate this map every render so duplicate-heading suffixes are stable.
	const components = {
		a({
			node: _node,
			href,
			...props
		}: { node?: unknown; href?: string; [k: string]: unknown }) {
			const isHash = typeof href === "string" && href.startsWith("#");
			return (
				<a
					{...props}
					href={href}
					target={isHash ? undefined : "_blank"}
					rel={isHash ? undefined : "noopener noreferrer nofollow"}
					onClick={(event) => {
						if (href && scrollToHash(href, event)) return;
						const onClick = props.onClick;
						if (typeof onClick === "function") onClick(event);
					}}
				/>
			);
		},
		h1({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h1 {...props} id={id}>{children}</h1>;
		},
		h2({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h2 {...props} id={id}>{children}</h2>;
		},
		h3({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h3 {...props} id={id}>{children}</h3>;
		},
		h4({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h4 {...props} id={id}>{children}</h4>;
		},
		h5({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h5 {...props} id={id}>{children}</h5>;
		},
		h6({
			node,
			children,
			...props
		}: { node?: unknown; children?: ReactNode; [k: string]: unknown }) {
			const offset = nodeStartOffset(node);
			const id =
				(offset != null ? headingIds.get(offset) : null) ??
				githubHeadingSlug(textFromChildren(children));
			return <h6 {...props} id={id}>{children}</h6>;
		},
		img({
			node: _node,
			src,
			...props
		}: { node?: unknown; src?: string; [k: string]: unknown }) {
			const resolved = resolveDocImageSrc(src, workspaceId, imgDocPath);
			// biome-ignore lint/a11y/useAltText: alt comes through ...props from md
			return <img src={resolved} {...props} />;
		},
	};
	return (
		<div className="h-full flex flex-col bg-[var(--color-bg)]">
			{(name || onEdit) && (
				<div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
					<FileText
						size={12}
						className="text-[var(--color-accent)] flex-shrink-0"
					/>
					<span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">
						{name}
					</span>
					{onEdit && (
						<button
							type="button"
							onClick={onEdit}
							title="编辑此文档"
							className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
						>
							<Pencil size={11} /> 编辑
						</button>
					)}
				</div>
			)}
			<div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
				<style>{DOC_STYLE}</style>
				<div className="pn-md-doc mx-auto max-w-[760px] px-5 py-7 sm:px-8">
					<MdBoundary content={content}>
						<ReactMarkdown
							remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
							rehypePlugins={[
								[rehypeHighlight, { detect: true, ignoreMissing: true }],
							]}
							// biome-ignore lint/suspicious/noExplicitAny: rmd component map
							components={components as any}
						>
							{fixedContent}
						</ReactMarkdown>
					</MdBoundary>
				</div>
			</div>
		</div>
	);
}
