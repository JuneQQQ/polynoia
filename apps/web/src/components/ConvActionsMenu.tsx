/** ConvActionsMenu — the per-conversation ⋮ menu in the sidebar list.
 *
 * Edit some info about a chat + delete it. The dropdown is rendered in a PORTAL
 * with fixed positioning (computed from the button's rect) so it floats above
 * everything and never grows/reflows the row or the panel it sits in. Actions:
 *   - 置顶/取消置顶 (all): pin sorts the conversation to the top of the list.
 *   - 重命名 (all): rename the conversation title (broadcasts the new title so
 *     an OPEN conversation's header updates immediately).
 *   - 成员与角色 (groups): opens the roles editor via `polynoia:edit-conv-roles`.
 *   - 归档 / 删除 (all): app-styled ConfirmDialog (not window.confirm).
 *
 * The ⋮ reveals on hover on pointer devices, but is ALWAYS visible on touch
 * (`@media (hover:none)`) and on keyboard focus — hover-only reveal is
 * unreachable on phones.
 */
import { Archive, MoreVertical, Pencil, Pin, PinOff, Settings, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type ConversationSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";
import { ConfirmDialog } from "./ConfirmDialog";

export function ConvActionsMenu({
	conv,
	onChanged,
}: {
	conv: ConversationSummary;
	/** Called after any mutation so the list can refresh. */
	onChanged: () => void;
}) {
	const lang = useStore((s) => s.lang);
	const [open, setOpen] = useState(false);
	const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
	const [renaming, setRenaming] = useState(false);
	const [confirming, setConfirming] = useState<"delete" | "archive" | null>(null);
	const [busy, setBusy] = useState(false);
	const btnRef = useRef<HTMLButtonElement | null>(null);

	useEffect(() => {
		if (!open) return;
		const close = () => setOpen(false);
		window.addEventListener("click", close);
		window.addEventListener("resize", close);
		window.addEventListener("scroll", close, true);
		return () => {
			window.removeEventListener("click", close);
			window.removeEventListener("resize", close);
			window.removeEventListener("scroll", close, true);
		};
	}, [open]);

	const toggle = (e: React.MouseEvent) => {
		e.stopPropagation();
		const r = btnRef.current?.getBoundingClientRect();
		if (r) {
			const W = 172;
			const H = conv.group ? 200 : 166;
			setPos({
				left: Math.max(8, Math.min(r.right - W, window.innerWidth - W - 8)),
				top: Math.max(8, Math.min(r.bottom + 4, window.innerHeight - H - 8)),
			});
		}
		setOpen((v) => !v);
	};

	const broadcast = () => {
		window.dispatchEvent(
			new CustomEvent("polynoia:conv-updated", { detail: { convId: conv.id } }),
		);
		window.dispatchEvent(new Event("polynoia:resync-lists"));
		onChanged();
	};

	const del = async () => {
		setConfirming(null);
		setBusy(true);
		try {
			await api.deleteConv(conv.id);
			window.dispatchEvent(
				new CustomEvent("polynoia:conv-deleted", { detail: { convId: conv.id } }),
			);
			broadcast();
		} catch (e) {
			window.alert(`${t("convDelete", lang)}: ${e instanceof Error ? e.message : e}`);
		} finally {
			setBusy(false);
		}
	};

	const archive = async () => {
		setConfirming(null);
		setBusy(true);
		try {
			await api.archiveConv(conv.id);
			window.dispatchEvent(
				new CustomEvent("polynoia:conv-archived", { detail: { convId: conv.id } }),
			);
			broadcast();
		} catch (e) {
			window.alert(`${t("convArchive", lang)}: ${e instanceof Error ? e.message : e}`);
		} finally {
			setBusy(false);
		}
	};

	const editRoles = () => {
		setOpen(false);
		window.dispatchEvent(
			new CustomEvent("polynoia:edit-conv-roles", { detail: { convId: conv.id } }),
		);
	};

	const togglePin = async () => {
		setOpen(false);
		setBusy(true);
		try {
			if (conv.pinned) await api.unpinConv(conv.id);
			else await api.pinConv(conv.id);
			broadcast();
		} catch (e) {
			window.alert(`${t("convPin", lang)}: ${e instanceof Error ? e.message : e}`);
		} finally {
			setBusy(false);
		}
	};

	return (
		<>
			<button
				ref={btnRef}
				type="button"
				onClick={toggle}
				disabled={busy}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label={t("convActionsLabel", lang)}
				title={t("convActionsLabel", lang)}
				className={`flex-shrink-0 p-1 rounded transition-opacity focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-[var(--color-accent)] [@media(hover:none)]:opacity-100 ${
					open ? "opacity-100" : "opacity-0 group-hover:opacity-100"
				} hover:bg-[var(--color-sidebar-active)]`}
			>
				<MoreVertical size={15} className="text-[var(--color-sidebar-muted)]" />
			</button>
			{open &&
				pos &&
				createPortal(
					<div
						role="menu"
						onClick={(e) => e.stopPropagation()}
						style={{ position: "fixed", left: pos.left, top: pos.top }}
						className="z-[100] min-w-[172px] rounded-md border border-[var(--color-line)] bg-[var(--color-surface)] shadow-lg py-1 anim-modal-in"
					>
						<MenuItem
							icon={conv.pinned ? <PinOff size={13} /> : <Pin size={13} />}
							label={conv.pinned ? t("convUnpin", lang) : t("convPin", lang)}
							onClick={togglePin}
						/>
						<MenuItem
							icon={<Pencil size={13} />}
							label={t("convRename", lang)}
							onClick={() => {
								setOpen(false);
								setRenaming(true);
							}}
						/>
						{conv.group && (
							<MenuItem
								icon={<Settings size={13} />}
								label={t("convMembersRoles", lang)}
								onClick={editRoles}
							/>
						)}
						<MenuItem
							icon={<Archive size={13} />}
							label={t("convArchive", lang)}
							onClick={() => {
								setOpen(false);
								setConfirming("archive");
							}}
						/>
						<div className="my-1 h-px bg-[var(--color-line)]" />
						<MenuItem
							icon={<Trash2 size={13} />}
							label={t("convDelete", lang)}
							danger
							onClick={() => {
								setOpen(false);
								setConfirming("delete");
							}}
						/>
					</div>,
					document.body,
				)}
			{confirming === "delete" && (
				<ConfirmDialog
					title={t("confirmDeleteConvTitle", lang)}
					body={t("confirmDeleteConvBody", lang).replace("{title}", conv.title)}
					confirmLabel={t("delete", lang)}
					cancelLabel={t("cancel", lang)}
					danger
					onConfirm={del}
					onCancel={() => setConfirming(null)}
				/>
			)}
			{confirming === "archive" && (
				<ConfirmDialog
					title={t("confirmArchiveConvTitle", lang)}
					body={t("confirmArchiveConvBody", lang).replace("{title}", conv.title)}
					confirmLabel={t("convArchive", lang)}
					cancelLabel={t("cancel", lang)}
					onConfirm={archive}
					onCancel={() => setConfirming(null)}
				/>
			)}
			{renaming && (
				<RenameModal
					conv={conv}
					onClose={() => setRenaming(false)}
					onDone={(newTitle) => {
						setRenaming(false);
						// Rich detail so App can optimistically update an OPEN conv's
						// header title without waiting for the snapshot refetch.
						window.dispatchEvent(
							new CustomEvent("polynoia:conv-renamed", {
								detail: { convId: conv.id, title: newTitle },
							}),
						);
						broadcast();
					}}
				/>
			)}
		</>
	);
}

function MenuItem({
	icon,
	label,
	onClick,
	danger,
}: {
	icon: React.ReactNode;
	label: string;
	onClick: () => void;
	danger?: boolean;
}) {
	return (
		<button
			type="button"
			role="menuitem"
			onClick={onClick}
			className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[12.5px] text-left transition ${
				danger
					? "text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40"
					: "text-[var(--color-fg-2)] hover:bg-[var(--color-sidebar-hover)]"
			}`}
		>
			<span className={danger ? "" : "text-[var(--color-fg-3)]"}>{icon}</span>
			{label}
		</button>
	);
}

function RenameModal({
	conv,
	onClose,
	onDone,
}: {
	conv: ConversationSummary;
	onClose: () => void;
	onDone: (newTitle: string) => void;
}) {
	const lang = useStore((s) => s.lang);
	const [title, setTitle] = useState(conv.title);
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);

	useEffect(() => {
		const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose]);

	const save = async () => {
		const tr = title.trim();
		if (!tr || busy) return;
		setBusy(true);
		setErr(null);
		try {
			await api.renameConv(conv.id, tr);
			onDone(tr);
		} catch (e) {
			setErr(e instanceof Error ? e.message : String(e));
			setBusy(false);
		}
	};

	return createPortal(
		<div
			className="fixed inset-0 z-[110] bg-black/40 flex items-center justify-center p-4"
			onClick={onClose}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[400px] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="font-display text-[16px] font-medium text-[var(--color-fg)] tracking-wide">
						{t("renameConvTitle", lang)}
					</div>
					<button
						type="button"
						onClick={onClose}
						className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
					>
						<X size={14} />
					</button>
				</header>
				<div className="px-5 py-4">
					<input
						// biome-ignore lint/a11y/noAutofocus: focus the field on open
						autoFocus
						type="text"
						value={title}
						onChange={(e) => setTitle(e.target.value)}
						onKeyDown={(e) => e.key === "Enter" && save()}
						// Select the prefilled title on focus — typing REPLACES it
						// (without this, typing appends: 文澜 → 文澜文澜·新名).
						onFocus={(e) => e.currentTarget.select()}
						maxLength={200}
						className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
					/>
					{err && (
						<div className="mt-2 text-[11.5px] text-[var(--color-red)]">{err}</div>
					)}
				</div>
				<div className="px-5 py-4 border-t border-[var(--color-line)] flex items-center justify-end gap-3">
					<button
						type="button"
						onClick={onClose}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						{t("cancel", lang)}
					</button>
					<button
						type="button"
						onClick={save}
						disabled={!title.trim() || busy}
						className="btn-primary"
					>
						{busy ? t("saving", lang) : t("save", lang)}
					</button>
				</div>
			</div>
		</div>,
		document.body,
	);
}
