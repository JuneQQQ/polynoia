/** PreviewErrorBoundary — catches render/parse throws from the binary document
 * previewers (xlsx WorkbookPreview, docx/pptx OfficePreview) so a bad file
 * degrades to a graceful "预览失败" card with a download fallback INSTEAD of
 * crashing the whole app (the "xlsx 点击直接崩溃" bug). React error boundaries
 * must be class components.
 */
import { Component, type ReactNode } from "react";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";

type Props = {
	children: ReactNode;
	/** Optional download URL shown in the fallback so the user can still get the file. */
	downloadHref?: string;
	fileName?: string;
};

type State = { error: Error | null };

export class PreviewErrorBoundary extends Component<Props, State> {
	state: State = { error: null };

	static getDerivedStateFromError(error: Error): State {
		return { error };
	}

	componentDidUpdate(prev: Props) {
		// Reset when the previewed file changes (so a new file gets a fresh try).
		if (prev.children !== this.props.children && this.state.error) {
			this.setState({ error: null });
		}
	}

	render() {
		if (this.state.error) {
			return (
				<FailedCard
					downloadHref={this.props.downloadHref}
					fileName={this.props.fileName}
					message={this.state.error.message}
				/>
			);
		}
		return this.props.children;
	}
}

function FailedCard({
	downloadHref,
	fileName,
	message,
}: {
	downloadHref?: string;
	fileName?: string;
	message: string;
}) {
	const lang = useStore((s) => s.lang);
	return (
		<div className="h-full grid place-items-center bg-[var(--color-surface-2)] p-8">
			<div className="max-w-[360px] text-center">
				<div className="text-[13px] font-medium text-[var(--color-fg)] mb-1.5">
					{t("previewFailed", lang)}
				</div>
				<div className="text-[11.5px] text-[var(--color-fg-3)] leading-relaxed mb-3">
					{t("previewFailedHint", lang)}
				</div>
				{downloadHref && (
					<a
						href={downloadHref}
						download={fileName}
						className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md bg-[var(--color-accent)] text-white text-[12px] font-medium no-underline"
					>
						{t("download", lang)}
						{fileName ? ` ${fileName}` : "文件"}
					</a>
				)}
				<div className="mt-2 text-[10px] font-mono text-[var(--color-fg-4)] truncate">
					{message}
				</div>
			</div>
		</div>
	);
}
