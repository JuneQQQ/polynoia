/** MobileMarkdownView — 移动端只读 Markdown 预览。
 *
 * 桌面端的 .md 预览用 CrepeEditor(Milkdown 的可编辑 WYSIWYG,懒加载、较重)。
 * 它在部分 Android WebView 里会崩 / 懒加载模块失败(报 "Failed to fetch
 * dynamically imported module")。移动端是只读预览,不需要编辑器 —— 直接用
 * 项目里聊天气泡同款的 react-markdown(remark-gfm + rehype-highlight + 同一套
 * MARKDOWN_COMPONENTS),轻量、已在 WebView 验证可用。
 */
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { MARKDOWN_COMPONENTS, fixCjkMarkdown } from "../parts/TextPart";

export function MobileMarkdownView({ content }: { content: string }) {
	return (
		<div className="h-full w-full overflow-y-auto bg-[var(--color-bg)] px-4 py-4 text-[15px] leading-relaxed text-[var(--color-fg)]">
			<ReactMarkdown
				remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
				rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
				// biome-ignore lint/suspicious/noExplicitAny: shared component map
				components={MARKDOWN_COMPONENTS as any}
			>
				{fixCjkMarkdown(content)}
			</ReactMarkdown>
		</div>
	);
}
