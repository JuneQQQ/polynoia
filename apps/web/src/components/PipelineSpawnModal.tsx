/** 流水线启动器 — gstack 式冲刺模板一键组队(复用既有联系人,缺口现雇)。
 * 产物:独立工作区 + 群聊(SOP 已写入草稿,用户补需求后回车开跑)。 */
import { Loader2, Rocket, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";

type Template = {
	key: string;
	name: string;
	description: string;
	stages: string[];
	slots: string[];
};

export function PipelineSpawnModal({ onClose }: { onClose: () => void }) {
	const lang = useStore((s) => s.lang);
	const [templates, setTemplates] = useState<Template[] | null>(null);
	const [busy, setBusy] = useState<string | null>(null);
	const [err, setErr] = useState("");

	useEffect(() => {
		api
			.pipelines()
			.then((r) => setTemplates(r.templates))
			.catch((e) => setErr(String(e)));
	}, []);

	const spawn = async (key: string) => {
		setBusy(key);
		setErr("");
		try {
			const res = await api.pipelineSpawn(key);
			const c = res.conversation;
			window.dispatchEvent(new CustomEvent("polynoia:resync-lists"));
			window.dispatchEvent(
				new CustomEvent("polynoia:select-conv", {
					detail: { id: c.id, members: c.members, title: c.title },
				}),
			);
			onClose();
		} catch (e) {
			setErr(`启动失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setBusy(null);
		}
	};

	return createPortal(
		<div
			className="fixed inset-0 z-[82] grid place-items-center bg-black/55 backdrop-blur-[2px]"
			role="dialog"
			aria-label={t("projectPipeline", lang)}
		>
			<div className="w-[min(560px,92vw)] rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-2xl overflow-hidden">
				<div className="flex items-center gap-2.5 px-4 py-3 border-b border-[var(--color-line)]">
					<Rocket size={15} className="text-[var(--color-accent)]" />
					<h2 className="text-[14px] font-semibold text-[var(--color-fg)]">
						{t("projectPipeline", lang)}
					</h2>
					<span className="text-[10.5px] text-[var(--color-fg-3)]">
						{t("pipelineSubtitle", lang)}
					</span>
					<span className="flex-1" />
					<button
						type="button"
						onClick={onClose}
						aria-label={t("close", lang)}
						className="p-1.5 rounded hover:bg-[var(--color-line)]/50 text-[var(--color-fg-3)]"
					>
						<X size={15} />
					</button>
				</div>
				{err && (
					<div className="px-4 py-2 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/30">
						{err}
					</div>
				)}
				<div className="p-4 space-y-3">
					{templates === null ? (
						<div className="grid place-items-center py-8">
							<Loader2
								size={16}
								className="animate-spin text-[var(--color-fg-3)]"
							/>
						</div>
					) : (
						templates.map((tpl) => (
							<div
								key={tpl.key}
								className="rounded-xl border border-[var(--color-line)] p-3.5 space-y-2 bg-[var(--color-bg)]/40"
							>
								<div className="flex items-center gap-2">
									<span className="text-[13px] font-medium text-[var(--color-fg)]">
										{tpl.name}
									</span>
									<span className="text-[10.5px] text-[var(--color-fg-3)]">
										{tpl.description}
									</span>
								</div>
								<div className="flex flex-wrap gap-1">
									{tpl.stages.map((s, i) => (
										<span
											key={s}
											className="text-[9.5px] px-1.5 py-0.5 rounded-full bg-[var(--color-line)]/50 text-[var(--color-fg-2)]"
										>
											{i + 1}.{s.split(" ")[0]}
										</span>
									))}
								</div>
								<div className="flex items-center gap-2">
									<span className="text-[10.5px] text-[var(--color-fg-3)]">
										角色:{tpl.slots.join(" · ")}
									</span>
									<span className="flex-1" />
									<button
										type="button"
										onClick={() => void spawn(tpl.key)}
										disabled={busy !== null}
										className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--color-accent)] text-white text-[11.5px] hover:opacity-90 disabled:opacity-60"
									>
										{busy === tpl.key ? (
											<Loader2 size={12} className="animate-spin" />
										) : (
											<Rocket size={12} />
										)}
										{t("teamUpAndLaunch", lang)}
									</button>
								</div>
							</div>
						))
					)}
					<p className="text-[10px] text-[var(--color-fg-3)] leading-relaxed">
						{t("teamingRules", lang)}
					</p>
				</div>
			</div>
		</div>,
		document.body,
	);
}
