/** Composer — 消息输入框 + @-mention picker + 工具栏
 *
 * @-picker 行为(模仿 Slack / Linear):
 *   - 输入 "@" 时弹出 picker
 *   - 实时 fuzzy filter:"@cl" → ClaudeCode / Orchestrator(@orc)等命中
 *   - ↑↓ 选,Enter / Tab 插入,Esc 关闭
 *   - 插入后光标位置正确;同一行可多次 @
 *   - picker 列表:本 conv 的 members + 所有 enabled adapter agents(全局可召唤)
 */
import { ArrowUp, FileText, Loader2, Paperclip, Reply, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { isMobile } from "../lib/platform";
import type { Agent } from "../lib/types";
import { useStore } from "../store";

type Props = {
	onSend: (text: string, inReplyTo?: string) => void;
	members: string[];
	/** Active conv id — used to scope the global replyingTo state to THIS
	 * conv (so switching convs doesn't show a stale reply chip). */
	convId: string;
	/** Pasted image — Composer converts blob to data URL and forwards.
	 * Optional: if not provided, paste falls through to default behavior. */
	onAttachImage?: (payload: {
		kind: "image";
		src: string;
		name?: string;
		media_type?: string;
	}) => void;
	/** Pasted / picked non-image file. Same data-URL contract as image. */
	onAttachFile?: (payload: {
		kind: "file";
		src: string;
		name: string;
		media_type?: string;
		size_bytes?: number;
	}) => void;
	/** Merge-mode toggle (workspace group convs only). Logic stays in ChatPane;
	 * Composer just renders the Auto/Manual control in its docked bar. */
	showMergeToggle?: boolean;
	mergeMode?: "auto" | "manual";
	onToggleMergeMode?: () => void;
	/** Live agent-running status, rendered as a strip docked just above the
	 * input box (inside the composer chrome) so it never floats over / occludes
	 * message content. Built by ChatPane (it owns the agent-status state). */
	statusSlot?: React.ReactNode;
};

/**
 * Find an "@<query>" token where the caret sits inside it.
 *
 * @ must be at the start of the input, after whitespace, or after a newline
 * — not in the middle of an email-like string. Returns the start position of
 * "@" and the current query (chars after @), or null if no active @ context.
 */
function detectMentionContext(
	value: string,
	caret: number,
): {
	atIndex: number;
	query: string;
} | null {
	// Scan backwards from caret to find an @ that begins a mention.
	for (let i = caret - 1; i >= 0; i--) {
		const ch = value[i];
		// Stop at whitespace / punctuation that ends a mention candidate
		if (ch === " " || ch === "\n" || ch === "\t") return null;
		if (ch === "@") {
			// Ensure @ is at start or preceded by whitespace (avoid email patterns)
			const prev = i === 0 ? " " : value[i - 1];
			if (prev !== " " && prev !== "\n" && prev !== "\t" && i !== 0)
				return null;
			return { atIndex: i, query: value.slice(i + 1, caret) };
		}
	}
	return null;
}

/** Score-based fuzzy match. Higher = better. Returns -1 if no match. */
function fuzzyScore(needle: string, hay: string): number {
	if (!needle) return 1;
	const n = needle.toLowerCase();
	const h = hay.toLowerCase();
	if (h === n) return 100;
	if (h.startsWith(n)) return 80;
	if (h.includes(n)) return 60;
	// letter-by-letter subsequence(用于 "cc" 匹配 "claudeCode")
	let i = 0;
	let j = 0;
	let lastJ = -1;
	while (i < n.length && j < h.length) {
		if (n[i] === h[j]) {
			if (lastJ >= 0 && j - lastJ > 4) return -1; // 字符跨度太大不算
			lastJ = j;
			i++;
		}
		j++;
	}
	return i === n.length ? 40 : -1;
}

export function Composer({
	onSend,
	members,
	convId,
	onAttachImage,
	onAttachFile,
	showMergeToggle = false,
	mergeMode = "auto",
	onToggleMergeMode,
	statusSlot,
}: Props) {
	const [text, setText] = useState("");
	// Mobile: roomier pill, bigger tap targets, and a 16px textarea (anything
	// smaller makes iOS Safari auto-zoom on focus). Desktop density unchanged.
	const mobile = isMobile();
	// Pending workspace-file refs (drag-dropped from FileTree). Each chip is
	// rendered above the textarea with an × to remove. On send we fan them
	// out via the existing onAttachFile callback (one file message per ref)
	// BEFORE the text message, so the agent sees the attachments first.
	const [pendingFileRefs, setPendingFileRefs] = useState<
		Array<{ wsId: string; path: string; name: string; size?: number | null }>
	>([]);
	// Pasted / picked attachments (paperclip + paste). Staged as chips ABOVE the
	// textarea — NOT sent on paste. The bytes upload immediately (so the chip can
	// preview + the send is instant), but the message is emitted only on submit,
	// BEFORE the text. `src` is null while uploading. This matches the user's ask:
	// "截图先追加到聊天框上面,再发送 — 不要直接发出去".
	const [pendingAtt, setPendingAtt] = useState<
		Array<{
			id: string;
			kind: "image" | "file";
			name: string;
			media_type?: string;
			size_bytes?: number;
			previewUrl?: string; // object URL for an instant image thumbnail
			src: string | null; // server URL once uploaded; null while uploading
		}>
	>([]);
	const attSeq = useRef(0);
	const anyUploading = pendingAtt.some((a) => a.src === null);
	// Lights up the composer outline while a drag is hovering it.
	const [isDragOver, setIsDragOver] = useState(false);
	const agents = useStore((s) => s.agents);
	const replyingToRaw = useStore((s) => s.replyingTo);
	const setReplyingTo = useStore((s) => s.setReplyingTo);
	// One-shot draft push from「从此处重来」(MessageView.rewindHere). When the
	// rewound message belonged to THIS conv, restore its text into the textarea
	// + clear the store so a later re-render doesn't re-apply on top of the
	// user's subsequent edits.
	const composerDraft = useStore((s) => s.composerDraft);
	const setComposerDraft = useStore((s) => s.setComposerDraft);
	useEffect(() => {
		if (!composerDraft || composerDraft.convId !== convId) return;
		setText(composerDraft.text);
		setComposerDraft(null);
		// Defer focus to next tick so the textarea is rendered + sized.
		window.setTimeout(() => taRef.current?.focus(), 0);
	}, [composerDraft, convId, setComposerDraft]);
	// Only show reply chip when the global state targets THIS conv.
	const replyingTo =
		replyingToRaw && replyingToRaw.convId === convId ? replyingToRaw : null;
	const isGroup = members.length > 2;
	const otherId = members.find((m) => m !== "you");
	const otherAgent = otherId ? agents.find((a) => a.id === otherId) : null;
	const taRef = useRef<HTMLTextAreaElement>(null);

	// @-picker state
	const [mention, setMention] = useState<{
		atIndex: number;
		query: string;
	} | null>(null);
	const [pickerIdx, setPickerIdx] = useState(0);

	// Candidates pool — ONLY this conversation's members. You can't summon an
	// agent who isn't in the conv (add them via the members panel instead).
	// In a 1v1 direct chat there are no other members to @, so the picker is
	// empty and never opens — @ is meaningless when talking to a single agent.
	const candidates: Agent[] = useMemo(() => {
		if (!isGroup) return [];
		const out = new Map<string, Agent>();
		for (const id of members) {
			if (id === "you" || id === "system") continue;
			const a = agents.find((x) => x.id === id);
			if (a) out.set(a.id, a);
		}
		return Array.from(out.values());
	}, [members, agents, isGroup]);

	// Filtered + scored candidates given current query
	const filtered = useMemo(() => {
		if (!mention) return [] as Agent[];
		const q = mention.query;
		const scored = candidates
			.map((a) => {
				const score = Math.max(
					fuzzyScore(q, a.id),
					fuzzyScore(q, a.name),
					fuzzyScore(q, a.handle?.replace(/^@/, "") ?? ""),
					fuzzyScore(q, a.role ?? ""),
				);
				return { a, score };
			})
			.filter((x) => x.score > 0)
			.sort((x, y) => y.score - x.score);
		return scored.slice(0, 8).map((x) => x.a);
	}, [mention, candidates]);

	// Keep pickerIdx valid as filtered changes
	useEffect(() => {
		if (filtered.length === 0) setPickerIdx(0);
		else if (pickerIdx >= filtered.length) setPickerIdx(0);
	}, [filtered, pickerIdx]);

	const submit = () => {
		const t = text.trim();
		// Don't ship while an attachment is still uploading (send button is also
		// disabled, but Enter could reach here).
		if (anyUploading) return;
		const readyAtt = pendingAtt.filter((a) => a.src);
		// Empty submit allowed ONLY when there are staged attachments to ship.
		if (!t && readyAtt.length === 0 && pendingFileRefs.length === 0) return;
		// Pasted / picked attachments: emit each (image or file) BEFORE the text so
		// the agent sees them first, then clear the staged chips.
		for (const a of readyAtt) {
			if (a.kind === "image" && onAttachImage) {
				onAttachImage({
					kind: "image",
					src: a.src as string,
					name: a.name,
					media_type: a.media_type,
				});
			} else if (onAttachFile) {
				onAttachFile({
					kind: "file",
					src: a.src as string,
					name: a.name,
					media_type: a.media_type,
					size_bytes: a.size_bytes,
				});
			}
			if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
		}
		if (readyAtt.length > 0) setPendingAtt([]);
		// Drag-dropped workspace files: emit each as its own file message FIRST,
		// so it lands in the timeline ahead of the text the agent gets routed.
		// Uses the same onAttachFile path the paperclip + paste flows already
		// use — ChatPane handles append + persist. URL points at the workspace
		// download endpoint, which FilePart already knows how to preview/download.
		if (pendingFileRefs.length > 0 && onAttachFile) {
			for (const ref of pendingFileRefs) {
				const src =
					`/api/workspaces/${encodeURIComponent(ref.wsId)}` +
					`/files/download?path=${encodeURIComponent(ref.path)}`;
				onAttachFile({
					kind: "file",
					src,
					name: ref.name,
					size_bytes: ref.size ?? undefined,
				});
			}
			setPendingFileRefs([]);
		}
		if (t) {
			onSend(t, replyingTo?.msgId);
		}
		setText("");
		setMention(null);
		if (replyingTo) setReplyingTo(null);
	};

	// Drag-drop a workspace file from the right FileTree into the composer.
	// Source side sets dataTransfer with `application/x-polynoia-file` carrying
	// {wsId, path, name, size}; we read it in onDrop and append a chip. We do
	// NOT auto-upload — the file is already in the workspace; we just reference
	// it via the workspace download URL when the user hits send.
	const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
		if (e.dataTransfer.types.includes("application/x-polynoia-file")) {
			e.preventDefault();
			e.dataTransfer.dropEffect = "copy";
			if (!isDragOver) setIsDragOver(true);
		}
	};
	const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
		// Only clear when the drag leaves the container itself, not when it
		// crosses a child boundary (relatedTarget stays inside).
		if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
			setIsDragOver(false);
		}
	};
	const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
		setIsDragOver(false);
		const raw = e.dataTransfer.getData("application/x-polynoia-file");
		if (!raw) return;
		e.preventDefault();
		try {
			const parsed = JSON.parse(raw) as {
				wsId: string;
				path: string;
				name?: string;
				size?: number | null;
			};
			if (!parsed.wsId || !parsed.path) return;
			const name = parsed.name || parsed.path.split("/").pop() || parsed.path;
			setPendingFileRefs((prev) => {
				// Dedupe by (wsId,path) so dragging the same file twice doesn't
				// create two chips / two file messages.
				if (
					prev.some((r) => r.wsId === parsed.wsId && r.path === parsed.path)
				) {
					return prev;
				}
				return [
					...prev,
					{
						wsId: parsed.wsId,
						path: parsed.path,
						name,
						size: parsed.size ?? null,
					},
				];
			});
		} catch {
			// ignore bad JSON
		}
	};

	const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
		const v = e.target.value;
		setText(v);
		const caret = e.target.selectionStart ?? v.length;
		setMention(detectMentionContext(v, caret));
	};

	// Stage an attachment: upload the bytes now (short URL, NOT a fat base64
	// data: URL — keeps DB rows small + re-renders after refresh from
	// /api/files/<id>/raw), but DON'T emit a message — push a pending chip the
	// user reviews above the textarea and ships on submit. 25MB cap = server's.
	const stageAttachment = async (file: File) => {
		if (file.size > 25 * 1024 * 1024) {
			window.alert(`${file.name} 超过 25MB,未上传`);
			return;
		}
		const id = `att-${attSeq.current++}`;
		const isImg = file.type.startsWith("image/");
		const previewUrl = isImg ? URL.createObjectURL(file) : undefined;
		setPendingAtt((p) => [
			...p,
			{
				id,
				kind: isImg ? "image" : "file",
				name: file.name || (isImg ? "pasted-image" : "attachment"),
				media_type: file.type || undefined,
				size_bytes: file.size,
				previewUrl,
				src: null,
			},
		]);
		try {
			const res = await api.upload(file, file.name || "attachment", convId);
			setPendingAtt((p) =>
				p.map((a) => (a.id === id ? { ...a, src: res.url } : a)),
			);
		} catch {
			setPendingAtt((p) => p.filter((a) => a.id !== id));
			if (previewUrl) URL.revokeObjectURL(previewUrl);
			window.alert(`上传失败:${file.name}`);
		}
	};

	const removePendingAtt = (id: string) => {
		setPendingAtt((p) => {
			const hit = p.find((a) => a.id === id);
			if (hit?.previewUrl) URL.revokeObjectURL(hit.previewUrl);
			return p.filter((a) => a.id !== id);
		});
	};

	const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
		const items = e.clipboardData?.items;
		if (!items) return;
		const fileItems = Array.from(items).filter((it) => it.kind === "file");
		if (fileItems.length === 0) return;
		e.preventDefault();
		for (const item of fileItems) {
			const file = item.getAsFile();
			if (file) stageAttachment(file);
		}
	};

	// Hidden <input type="file"> driven by the paperclip icon click.
	const fileInputRef = useRef<HTMLInputElement>(null);
	const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
		const files = e.target.files ? Array.from(e.target.files) : [];
		for (const f of files) stageAttachment(f);
		// Reset so picking the SAME file twice still triggers onChange.
		e.target.value = "";
	};

	const handleSelect = () => {
		// Caret moved (click / arrow keys) — re-detect mention context
		const ta = taRef.current;
		if (!ta) return;
		const caret = ta.selectionStart ?? text.length;
		setMention(detectMentionContext(text, caret));
	};

	const insertMention = (agent: Agent) => {
		if (!mention) return;
		const before = text.slice(0, mention.atIndex);
		const afterQueryStart = mention.atIndex + 1 + mention.query.length;
		const after = text.slice(afterQueryStart);
		// Insert the agent's display name (`@林知夏 ` not `@01KSQ...`). The
		// server's mention parser resolves names back to ids via conv members.
		// For template adapter agents the name and id are basically the same
		// (e.g. "Orchestrator" / "Claude Code"); for custom contacts the name
		// is human-readable while the id is a ULID — only the name is usable
		// in chat copy.
		const inserted = `@${agent.name} `;
		const next = before + inserted + after;
		setText(next);
		setMention(null);
		// Place caret right after the inserted token + trailing space
		const newCaret = before.length + inserted.length;
		requestAnimationFrame(() => {
			const ta = taRef.current;
			if (ta) {
				ta.focus();
				ta.setSelectionRange(newCaret, newCaret);
			}
		});
	};

	const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
		// IME composition (中文/日文/韩文输入法):合成期间 Enter/空格/方向键属于
		// 输入法(确认候选词 / 选词),在这里拦截会把合成吃掉 → 汉字录不进去。
		// 合成结束前完全让位给 IME。keyCode 229 是部分 Android WebView 的旧式信号。
		if (e.nativeEvent.isComposing || e.keyCode === 229) return;
		// Picker keyboard control
		if (mention && filtered.length > 0) {
			if (e.key === "ArrowDown") {
				e.preventDefault();
				setPickerIdx((i) => (i + 1) % filtered.length);
				return;
			}
			if (e.key === "ArrowUp") {
				e.preventDefault();
				setPickerIdx((i) => (i - 1 + filtered.length) % filtered.length);
				return;
			}
			if (e.key === "Enter" || e.key === "Tab") {
				e.preventDefault();
				const target = filtered[pickerIdx];
				if (target) insertMention(target);
				return;
			}
			if (e.key === "Escape") {
				e.preventDefault();
				setMention(null);
				return;
			}
		}
		// Normal submit
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			submit();
		}
	};

	// Auto-grow the textarea with content (ChatGPT/Claude feel), capped at 200px
	// then it scrolls internally. Presentation behavior only.
	useEffect(() => {
		const ta = taRef.current;
		if (!ta) return;
		ta.style.height = "auto";
		ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
	}, [text]);

	const placeholder = isGroup
		? "发消息给群聊 · 输入 @ 召唤成员"
		: `发消息给 ${otherAgent?.name ?? "Agent"}`;

	return (
		// No outer container/rectangle/bg at all — fully transparent so the rounded
		// input pill (below) visually FLOATS over the message stream (悬空). The pill
		// carries its own surface bg + shadow.
		<div className="bg-transparent">
			<div
				className={`relative ${mobile ? "px-2 pt-1.5 pb-2" : "px-6 pt-2 pb-3"}`}
			>
				{/* @-mention picker */}
				{mention && filtered.length > 0 && (
					<div className="absolute bottom-full left-5 right-5 mb-1 z-30 bg-[var(--color-surface)] border border-[var(--color-line)] rounded-lg shadow-lg overflow-hidden max-h-[280px] overflow-y-auto">
						<div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] border-b border-[var(--color-line)]/50 bg-[var(--color-surface-2)]">
							召唤 Agent — ↑↓ 选择 · Enter / Tab 插入 · Esc 取消
						</div>
						<ul>
							{filtered.map((a, i) => (
								<li key={a.id}>
									<button
										type="button"
										onMouseEnter={() => setPickerIdx(i)}
										onMouseDown={(e) => {
											// mouseDown before blur so caret stays
											e.preventDefault();
											insertMention(a);
										}}
										className={`flex items-center gap-2.5 w-full px-3 py-1.5 text-left transition ${
											i === pickerIdx
												? "bg-[var(--color-accent-soft)]"
												: "hover:bg-[var(--color-surface-2)]"
										}`}
									>
										<div
											className="w-7 h-7 rounded-md grid place-items-center text-white text-[10px] font-medium flex-shrink-0"
											style={{ background: a.color }}
										>
											{a.initials}
										</div>
										<div className="flex-1 min-w-0">
											<div className="text-[12.5px] font-medium truncate">
												{a.name}
												<span className="ml-1.5 text-[10.5px] text-[var(--color-fg-3)] font-normal">
													@{a.id}
												</span>
											</div>
											{(a.tagline || a.role) && (
												<div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
													{a.tagline ?? a.role}
												</div>
											)}
										</div>
										{members.includes(a.id) && (
											<span className="text-[9.5px] px-1.5 py-0.5 rounded bg-[var(--color-green-soft)] text-[var(--color-green)] flex-shrink-0">
												本群
											</span>
										)}
									</button>
								</li>
							))}
						</ul>
					</div>
				)}

				{/* Reply chip — appears above the textarea when user clicked Reply
            on a message. Shows sender + snippet + close. Consumed by ChatPane
            via the global replyingTo store state (cleared after send). */}
				{replyingTo && (
					<div className="mb-2 flex items-center gap-2 px-2.5 py-1.5 rounded-sm bg-[var(--color-accent-soft)] border-l-2 border-[var(--color-accent)] text-[11.5px] anim-fade-up">
						<Reply
							size={11}
							className="text-[var(--color-accent)] flex-shrink-0"
						/>
						<span className="text-[var(--color-accent)] font-medium flex-shrink-0">
							回复
						</span>
						<span className="font-medium text-[var(--color-fg-2)] flex-shrink-0">
							{replyingTo.senderLabel}
						</span>
						<span className="text-[var(--color-fg-3)] truncate min-w-0 flex-1">
							{replyingTo.snippet}
						</span>
						<button
							type="button"
							onClick={() => setReplyingTo(null)}
							className="flex-shrink-0 p-0.5 rounded-sm hover:bg-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
							title="取消回复"
						>
							<X size={11} />
						</button>
					</div>
				)}

				{/* Workspace-file attachment chips — drag-dropped from the right
            FileTree. Each chip shows name + ×; on send each becomes its own
            file message (BEFORE the user text). Distinct from paperclip/paste
            which post immediately — drag-drop is "compose then send". */}
				{pendingFileRefs.length > 0 && (
					<div className="mb-2 flex flex-wrap gap-1.5">
						{pendingFileRefs.map((ref) => (
							<div
								key={`${ref.wsId}/${ref.path}`}
								className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[11.5px] anim-fade-up"
								title={`workspace://${ref.wsId}/${ref.path}`}
							>
								<FileText
									size={11}
									className="text-[var(--color-fg-3)] flex-shrink-0"
								/>
								<span className="font-mono text-[var(--color-fg-2)] truncate max-w-[200px]">
									{ref.name}
								</span>
								<button
									type="button"
									onClick={() =>
										setPendingFileRefs((prev) =>
											prev.filter(
												(r) => !(r.wsId === ref.wsId && r.path === ref.path),
											),
										)
									}
									className="flex-shrink-0 p-0.5 rounded-sm hover:bg-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
									title="移除"
								>
									<X size={10} />
								</button>
							</div>
						))}
					</div>
				)}

				{/* Pasted / picked attachment chips — staged above the textarea, shipped
            on send (BEFORE the text), NOT on paste. Image → thumbnail, other →
            name; a spinner overlays while the bytes upload. */}
				{pendingAtt.length > 0 && (
					<div className="mb-2 flex flex-wrap gap-2">
						{pendingAtt.map((a) => (
							<div
								key={a.id}
								className="relative inline-flex items-center gap-1.5 rounded-md bg-[var(--color-surface-2)] border border-[var(--color-line)] anim-fade-up"
								title={a.name}
							>
								{a.kind === "image" && (a.previewUrl || a.src) ? (
									<img
										src={a.previewUrl || (a.src as string)}
										alt={a.name}
										className="h-14 w-14 object-cover rounded-md"
									/>
								) : (
									<span className="inline-flex items-center gap-1.5 px-2 py-1.5 text-[11.5px]">
										<FileText
											size={11}
											className="text-[var(--color-fg-3)] flex-shrink-0"
										/>
										<span className="font-mono text-[var(--color-fg-2)] truncate max-w-[160px]">
											{a.name}
										</span>
									</span>
								)}
								{a.src === null && (
									<span className="absolute inset-0 grid place-items-center rounded-md bg-[var(--color-surface)]/60">
										<Loader2
											size={16}
											className="animate-spin text-[var(--color-accent)]"
										/>
									</span>
								)}
								<button
									type="button"
									onClick={() => removePendingAtt(a.id)}
									className="absolute -top-1.5 -right-1.5 grid place-items-center w-4 h-4 rounded-full bg-[var(--color-fg-3)] text-white hover:bg-[var(--color-fg)] transition shadow"
									title="移除"
								>
									<X size={10} />
								</button>
							</div>
						))}
					</div>
				)}

				{/* Unified composer — ChatGPT/Claude style: one rounded container with
            the textarea on top and all controls (attach · mode · send) docked
            along its bottom edge. Focus lifts the whole box with an accent ring.
            onDragOver/onDrop here accept workspace-file drops from the right
            FileTree (custom MIME `application/x-polynoia-file`). */}
				<div
					onDragOver={handleDragOver}
					onDragLeave={handleDragLeave}
					onDrop={handleDrop}
					className={`border bg-[var(--color-surface)] shadow-[var(--shadow-card)] transition-colors duration-200 focus-within:border-[var(--color-accent)]/55 ${
						mobile
							? "rounded-[22px] px-2.5 pt-2 pb-2"
							: "rounded-[22px] px-2.5 pt-2 pb-2"
					} ${
						isDragOver
							? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]/40"
							: "border-[var(--color-line-strong)]"
					}`}
				>
					{/* Live agent-running status — part of the composer panel, not a
            floating overlay. Keeping it inside this chrome prevents it from
            visually colliding with the last message while agents stream. */}
					{statusSlot}

					<textarea
						ref={taRef}
						value={text}
						onChange={handleChange}
						onKeyUp={handleSelect}
						onClick={handleSelect}
						onKeyDown={handleKeyDown}
						onPaste={handlePaste}
						placeholder={placeholder}
						rows={1}
						className={`w-full resize-none bg-transparent outline-none leading-relaxed text-[var(--color-fg)] placeholder:text-[var(--color-fg-4)] max-h-[200px] ${
							mobile
								? "text-[16px] min-h-[28px] px-2 py-1.5"
								: "text-[14px] min-h-[40px] px-2 py-1.5"
						}`}
					/>
					{/* Docked control bar — sits INSIDE the box, ChatGPT/Claude-style */}
					<div className="flex items-center gap-1.5 px-0.5">
						<input
							ref={fileInputRef}
							type="file"
							multiple
							className="hidden"
							onChange={onPickFiles}
						/>
						{/* "+" → add attachment (paste also works). Skills are bound at the
                CONTACT level now, not added per-message here. */}
						<button
							type="button"
							onClick={() => fileInputRef.current?.click()}
							className={`grid place-items-center rounded-full text-[var(--color-fg-3)] hover:text-[var(--color-accent)] hover:bg-[var(--color-surface-2)] transition-all duration-150 ${
								mobile ? "w-10 h-10" : "w-8 h-8"
							}`}
							title="添加附件(也支持粘贴)"
						>
							<Paperclip size={mobile ? 20 : 16} />
						</button>
						{/* Merge-mode toggle — relocated from the header into the composer */}
						{showMergeToggle && onToggleMergeMode && (
							<div
								className="flex items-stretch rounded-full border border-[var(--color-line)] overflow-hidden text-[10px] font-mono uppercase tracking-[0.16em] font-medium"
								role="group"
								aria-label="merge mode"
							>
								<button
									type="button"
									onClick={() => mergeMode !== "auto" && onToggleMergeMode()}
									className={`px-2.5 py-1 transition-colors duration-150 ${
										mergeMode === "auto"
											? "bg-[var(--color-accent)] text-white"
											: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
									}`}
									title="Auto · 子任务完成后自动合并到 main"
								>
									Auto
								</button>
								<button
									type="button"
									onClick={() => mergeMode !== "manual" && onToggleMergeMode()}
									className={`px-2.5 py-1 border-l border-[var(--color-line)] transition-colors duration-150 ${
										mergeMode === "manual"
											? "bg-[var(--color-accent)] text-white"
											: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
									}`}
									title="Manual · 每个 edit 都需你确认才落盘"
								>
									Manual
								</button>
							</div>
						)}
						{/* Recipient is already shown in the chat header — no redundant
                "{name} · 1v1" chip in the composer bar (kept clean). */}
						{/* Active when there's text OR at least one drag-dropped file ref —
                matches submit()'s own gate (Composer.tsx:172) so Enter and click
                behave the same; otherwise "drag a file, send empty" only worked
                via Enter. */}
						<button
							type="button"
							onClick={submit}
							disabled={
								anyUploading ||
								(!text.trim() &&
									pendingFileRefs.length === 0 &&
									pendingAtt.length === 0)
							}
							title={anyUploading ? "附件上传中…" : "发送 (Enter)"}
							className={`ml-auto grid place-items-center rounded-full transition-all duration-150 ${
								mobile ? "w-10 h-10" : "w-8 h-8"
							} ${
								!anyUploading &&
								(
									text.trim() ||
										pendingFileRefs.length > 0 ||
										pendingAtt.length > 0
								)
									? "bg-[var(--color-accent)] text-white hover:brightness-110 press-down"
									: "bg-[var(--color-surface-3)] text-[var(--color-fg-4)] cursor-not-allowed"
							}`}
						>
							{anyUploading ? (
								<Loader2 size={mobile ? 20 : 17} className="animate-spin" />
							) : (
								<ArrowUp size={mobile ? 20 : 17} strokeWidth={2.4} />
							)}
						</button>
					</div>
				</div>
			</div>
		</div>
	);
}
