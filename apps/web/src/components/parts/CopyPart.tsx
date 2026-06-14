import { Check, FileText } from "lucide-react";
import { useState } from "react";
import { t } from "../../lib/i18n";
import type { CopyPayload } from "../../lib/types";
import { useStore } from "../../store";

export function CopyPart({ payload }: { payload: CopyPayload }) {
	const lang = useStore((s) => s.lang);
	const [picked, setPicked] = useState(0);
	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[520px]">
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<FileText size={14} className="text-[var(--color-fg-3)]" />
				<span className="text-xs font-medium">
					{t("heroCopyCandidates", lang)
						.replace("{payload.hero.length}", String(payload.hero.length))
						.replace("{count}", String(payload.hero.length))}
				</span>
				<button
					type="button"
					className="ml-auto inline-flex items-center gap-1 text-[10.5px] text-[var(--color-accent)]"
				>
					<Check size={11} /> {t("adoptCurrent", lang)}
				</button>
			</div>
			<div className="p-3 space-y-1.5">
				{payload.hero.map((h, i) => {
					const active = i === picked;
					return (
						<button
							key={i}
							type="button"
							onClick={() => setPicked(i)}
							className={`w-full text-left flex items-start gap-2 px-2.5 py-2 rounded-md text-[12.5px] transition border ${
								active
									? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
									: "bg-[var(--color-surface)] border-[var(--color-line)]"
							}`}
						>
							<span
								className="w-3.5 h-3.5 mt-0.5 rounded-full flex-shrink-0 border-[1.5px]"
								style={{
									borderColor: active
										? "var(--color-accent)"
										: "var(--color-line-strong)",
									background: active ? "var(--color-accent)" : "transparent",
								}}
							/>
							<span>{h}</span>
						</button>
					);
				})}
				<div className="mt-2 px-2.5 py-2 bg-[var(--color-surface-2)] rounded-md flex gap-3 text-[11.5px] text-[var(--color-fg-3)]">
					<span>
						<b className="text-[var(--color-fg)]">CTA</b> {payload.cta.primary}
					</span>
					<span>
						<b className="text-[var(--color-fg)]">{t("secondary", lang)}</b>{" "}
						{payload.cta.secondary}
					</span>
				</div>
			</div>
		</div>
	);
}
