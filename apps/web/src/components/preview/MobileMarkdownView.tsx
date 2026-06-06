/** MobileMarkdownView — 移动端只读 Markdown 预览。
 *
 * 桌面端 .md 预览默认也用只读精排([[MarkdownDoc]]);移动端不需要编辑,所以直接
 * 复用同一套文档级排版组件(react-markdown + 暖色主题 + 错误兜底),三端观感一致。
 * 不传 path/onEdit → 不显示头栏,只是可滚动的文档正文。
 */
import { MarkdownDoc } from "./MarkdownDoc";

export function MobileMarkdownView({ content }: { content: string }) {
	return <MarkdownDoc content={content} />;
}
