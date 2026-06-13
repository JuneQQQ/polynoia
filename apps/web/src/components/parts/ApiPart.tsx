import { ArrowRight, Globe } from "lucide-react";
import { t } from "../../lib/i18n";
import type { ApiPayload } from "../../lib/types";
import { useStore } from "../../store";

const methodStyle = (m: string): { bg: string; fg: string } => {
	switch (m) {
		case "GET":
			return { bg: "var(--color-green-soft)", fg: "var(--color-green)" };
		case "POST":
			return { bg: "var(--color-blue-soft)", fg: "var(--color-blue)" };
		case "PUT":
			return { bg: "var(--color-amber-soft)", fg: "var(--color-amber)" };
		case "PATCH":
			return { bg: "var(--color-purple-soft)", fg: "var(--color-purple)" };
		case "DELETE":
			return { bg: "var(--color-red-soft)", fg: "var(--color-red)" };
		default:
			return { bg: "var(--color-line)", fg: "var(--color-fg-3)" };
	}
};

export function ApiPart({ payload }: { payload: ApiPayload }) {
	const lang = useStore((s) => s.lang);
	const ms = methodStyle(payload.method);
	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[680px]">
			{/* Header */}
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<Globe size={14} className="text-[var(--color-fg-3)]" />
				<span
					className="text-[10.5px] font-bold uppercase px-1.5 py-0.5 rounded mono"
					style={{ background: ms.bg, color: ms.fg }}
				>
					{payload.method}
				</span>
				<span className="text-xs font-medium mono truncate flex-1">
					{payload.path}
				</span>
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-medium"
					style={{
						background: "var(--color-green-soft)",
						color: "var(--color-green)",
					}}
				>
					{t("alreadyFixed", lang)}
				</span>
			</div>

			<div className="px-3 py-2 text-[12px] text-[var(--color-fg-3)] border-b border-[var(--color-line)]">
				{payload.desc}
			</div>

			{/* Params */}
			<table className="w-full text-[11.5px]">
				<thead>
					<tr className="text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] border-b border-[var(--color-line)]">
						<th className="text-left px-3 py-1.5 font-semibold">
							{t("parameter", lang)}
						</th>
						<th className="text-left px-3 py-1.5 font-semibold">
							{t("location", lang)}
						</th>
						<th className="text-left px-3 py-1.5 font-semibold">
							{t("type", lang)}
						</th>
						<th className="text-left px-3 py-1.5 font-semibold">
							{t("required", lang)}
						</th>
						<th className="text-left px-3 py-1.5 font-semibold">
							{t("example", lang)}
						</th>
					</tr>
				</thead>
				<tbody>
					{payload.params.map((p) => (
						<tr
							key={p.name}
							className="border-b border-[var(--color-line)]/60 last:border-0"
						>
							<td className="px-3 py-1.5 mono font-medium">{p.name}</td>
							<td className="px-3 py-1.5 mono text-[var(--color-fg-3)]">
								{p.in}
							</td>
							<td className="px-3 py-1.5 mono text-[var(--color-fg-3)]">
								{p.type}
							</td>
							<td className="px-3 py-1.5">
								{p.required ? (
									<span
										style={{ color: "var(--color-red)" }}
										className="font-bold"
									>
										✓
									</span>
								) : (
									<span className="text-[var(--color-fg-4)]">—</span>
								)}
							</td>
							<td className="px-3 py-1.5 mono text-[var(--color-fg-3)] text-[10.5px]">
								{p.eg ?? ""}
							</td>
						</tr>
					))}
				</tbody>
			</table>

			{/* Perf */}
			{payload.perf && (
				<div className="flex items-center gap-3 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11.5px]">
					<span className="text-[10.5px] text-[var(--color-fg-3)] uppercase tracking-wider font-semibold">
						{t("perfComparison", lang)}
					</span>
					<span
						className="mono px-1.5 py-0.5 rounded"
						style={{
							background: "var(--color-red-soft)",
							color: "var(--color-red)",
						}}
					>
						{payload.perf.before}
					</span>
					<ArrowRight size={11} className="text-[var(--color-fg-3)]" />
					<span
						className="mono px-1.5 py-0.5 rounded font-medium"
						style={{
							background: "var(--color-green-soft)",
							color: "var(--color-green)",
						}}
					>
						{payload.perf.after}
					</span>
					<span className="ml-auto text-[11px] font-semibold text-[var(--color-green)]">
						↓ 93%
					</span>
				</div>
			)}
		</div>
	);
}
