/** ConfirmDialog — app-styled replacement for window.confirm on destructive ops.
 *
 * Native confirm() is jarring, browser-locale-locked (ignores the app's 中/EN
 * setting) and breaks the modal-card visual language. This renders in a portal,
 * Escape cancels, Enter confirms.
 */
import { useEffect } from "react";
import { createPortal } from "react-dom";

export function ConfirmDialog({
	title,
	body,
	confirmLabel,
	cancelLabel,
	danger,
	onConfirm,
	onCancel,
}: {
	title: string;
	body?: string;
	confirmLabel: string;
	cancelLabel: string;
	danger?: boolean;
	onConfirm: () => void;
	onCancel: () => void;
}) {
	useEffect(() => {
		const h = (e: KeyboardEvent) => {
			if (e.key === "Escape") onCancel();
			if (e.key === "Enter") onConfirm();
		};
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onCancel, onConfirm]);

	return createPortal(
		<div
			className="fixed inset-0 z-[120] bg-black/40 flex items-center justify-center p-4"
			onClick={onCancel}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[380px]"
				onClick={(e) => e.stopPropagation()}
			>
				<div className="px-5 pt-5 pb-1 font-display text-[15.5px] font-medium text-[var(--color-fg)] leading-snug">
					{title}
				</div>
				{body && (
					<div className="px-5 pb-2 text-[12px] text-[var(--color-fg-3)] leading-relaxed whitespace-pre-line">
						{body}
					</div>
				)}
				<div className="px-5 py-4 flex items-center justify-end gap-3">
					<button
						type="button"
						onClick={onCancel}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						{cancelLabel}
					</button>
					<button
						type="button"
						onClick={onConfirm}
						className={
							danger
								? "text-[13px] px-3.5 py-1.5 rounded-md bg-[var(--color-red)] text-white font-medium hover:opacity-90 transition"
								: "btn-primary"
						}
					>
						{confirmLabel}
					</button>
				</div>
			</div>
		</div>,
		document.body,
	);
}
