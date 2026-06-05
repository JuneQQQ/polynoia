/** MobilePreviewSheet — 移动端全屏只读产物预览。
 *
 * 桌面端在右栏 PreviewPane 里预览文件;移动端是单列布局,不挂 PreviewPane,
 * 所以聊天里点文件卡(FilePart/FilesPanelPart → preview.open + previewFile +
 * data.workspaceId)原本是死路。这个 sheet 补上那一环:全屏推起一个只读
 * 阅读器。符合移动端「轻量 IM 子集」—— 只读,不做文件树/终端/编辑。
 *
 * 复用 RightPreviewFile,所以每种 docKind(代码/图片/xlsx/docx/pptx/md)的
 * 渲染与桌面完全一致;它自己用 workspaceFileBytes/Read 取字节(blob/raw 接口),
 * 在 WebView 内由 JS 库渲染 —— 不依赖原生下载。
 */
import { X } from "lucide-react";
import { useStore } from "../store";
import { RightPreviewFile } from "./preview/RightPreviewFile";

function basename(path: string): string {
	return path.split("/").pop() ?? path;
}

export function MobilePreviewSheet() {
	const open = useStore((s) => s.preview.open);
	const previewFile = useStore((s) => s.preview.previewFile);
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const closePreview = useStore((s) => s.closePreview);

	// 只接管「单文件只读预览」这条路径。冲突解决 / 评审 / 文件树(previewFile
	// 为 null)仍是桌面专属,移动端不弹此 sheet。
	if (!open || !previewFile || !workspaceId) return null;

	return (
		<div
			className="fixed inset-0 z-[60] flex flex-col bg-[var(--color-bg)] anim-fade-up"
			style={{ paddingTop: "env(safe-area-inset-top)" }}
		>
			<header className="flex items-center gap-2 px-3 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
				<div className="flex-1 min-w-0">
					<div className="text-[14px] font-semibold truncate text-[var(--color-fg)]">
						{basename(previewFile)}
					</div>
					<div className="text-[10.5px] font-mono text-[var(--color-fg-3)] truncate">
						{previewFile}
					</div>
				</div>
				<button
					type="button"
					onClick={closePreview}
					aria-label="关闭预览"
					className="w-10 h-10 grid place-items-center rounded-full text-[var(--color-fg-2)] hover:bg-[var(--color-surface-2)] press-down"
				>
					<X size={22} />
				</button>
			</header>
			<div className="flex-1 min-h-0 overflow-hidden">
				<RightPreviewFile workspaceId={workspaceId} path={previewFile} />
			</div>
		</div>
	);
}
