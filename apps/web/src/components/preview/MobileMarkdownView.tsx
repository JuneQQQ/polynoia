/** MobileMarkdownView — 移动端只读 Markdown 预览。
 *
 * 桌面端的 .md 预览用 CrepeEditor(Milkdown 的可编辑 WYSIWYG,懒加载、较重)。
 * 它在部分 Android WebView 里会崩 / 懒加载模块失败。移动端是只读预览,不需要
 * 编辑器 —— 用聊天气泡同款的 react-markdown(remark-gfm + rehype-highlight)。
 *
 * 兜底:万一 react-markdown 在某些 WebView 里渲染抛错,错误边界会退化成显示
 * 纯文本 markdown 源码,保证"至少能看到内容",不会整页空白。
 */
import { Component, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { MARKDOWN_COMPONENTS, fixCjkMarkdown } from "../parts/TextPart";

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

export function MobileMarkdownView({ content }: { content: string }) {
	return (
		<div className="h-full w-full overflow-y-auto bg-[var(--color-bg)] px-4 py-4 text-[15px] leading-relaxed text-[var(--color-fg)]">
			<MdBoundary content={content}>
				<ReactMarkdown
					remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
					rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
					// biome-ignore lint/suspicious/noExplicitAny: shared component map
					components={MARKDOWN_COMPONENTS as any}
				>
					{fixCjkMarkdown(content)}
				</ReactMarkdown>
			</MdBoundary>
		</div>
	);
}
