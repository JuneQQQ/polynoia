import { Check, Database, Plus, Sparkle } from "lucide-react";
import { useState } from "react";
import { t } from "../../lib/i18n";
import type { SchemaPayload } from "../../lib/types";
import { useStore } from "../../store";

export function SchemaPart({ payload }: { payload: SchemaPayload }) {
	const lang = useStore((s) => s.lang);
	const [generated, setGenerated] = useState<Set<string>>(new Set());

	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[640px]">
			{/* Header */}
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<Database size={14} className="text-[var(--color-fg-3)]" />
				<span className="text-xs font-medium mono truncate flex-1">
					{payload.table}
				</span>
				<span className="text-[10.5px] uppercase tracking-wider text-[var(--color-fg-3)]">
					schema
				</span>
			</div>

			{/* Fields table */}
			<div className="overflow-x-auto">
				<table className="w-full text-[11.5px]">
					<thead>
						<tr className="text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] border-b border-[var(--color-line)]">
							<th className="text-left px-3 py-1.5 font-semibold">
								{t("field", lang)}
							</th>
							<th className="text-left px-3 py-1.5 font-semibold">
								{t("type", lang)}
							</th>
							<th className="text-left px-3 py-1.5 font-semibold">
								{t("nullable", lang)}
							</th>
							<th className="text-left px-3 py-1.5 font-semibold">
								{t("key", lang)}
							</th>
						</tr>
					</thead>
					<tbody>
						{payload.fields.map((f) => (
							<tr
								key={f.name}
								className="border-b border-[var(--color-line)]/60 last:border-0"
							>
								<td className="px-3 py-1.5 mono font-medium">{f.name}</td>
								<td className="px-3 py-1.5 mono text-[var(--color-fg-3)]">
									{f.type}
								</td>
								<td className="px-3 py-1.5 text-[var(--color-fg-3)]">
									{f.null ? "—" : "✗"}
								</td>
								<td className="px-3 py-1.5">
									{f.key && (
										<span
											className="text-[9.5px] font-bold px-1.5 py-0.5 rounded"
											style={{
												background:
													f.key === "PK"
														? "var(--color-accent-soft)"
														: "var(--color-blue-soft)",
												color:
													f.key === "PK"
														? "var(--color-accent)"
														: "var(--color-blue)",
											}}
										>
											{f.key}
										</span>
									)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>

			{/* Indexes */}
			<div className="border-t border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2">
				<div className="text-[10.5px] text-[var(--color-fg-3)] uppercase tracking-wider mb-1.5 font-semibold">
					{t("indexes", lang)}
				</div>
				<div className="space-y-1">
					{payload.indexes.map((idx) => {
						const isGenerated = generated.has(idx.name);
						return (
							<div
								key={idx.name}
								className={`flex items-center gap-2 px-2 py-1.5 rounded text-[11.5px] ${
									idx.recommend
										? "border-2 border-dashed border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)]/30"
										: "bg-[var(--color-surface)]"
								}`}
							>
								<span
									className="text-[9.5px] font-bold px-1.5 py-0.5 rounded mono"
									style={{
										background: idx.recommend
											? "var(--color-accent)"
											: "var(--color-line)",
										color: idx.recommend ? "#fff" : "var(--color-fg-3)",
									}}
								>
									{idx.kind}
								</span>
								<span className="mono text-[var(--color-fg)]">{idx.name}</span>
								<span className="mono text-[10.5px] text-[var(--color-fg-3)] truncate">
									{idx.cols}
								</span>
								{idx.recommend && (
									<span
										className="ml-auto inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded"
										style={{
											background: "var(--color-accent-soft)",
											color: "var(--color-accent)",
										}}
									>
										<Sparkle size={10} /> {t("recommended", lang)}
									</span>
								)}
								{idx.existing && idx.note && (
									<span className="ml-auto text-[10px] text-[var(--color-amber)]">
										{idx.note}
									</span>
								)}
								{idx.recommend &&
									(isGenerated ? (
										<span
											className="inline-flex items-center gap-1 text-[10.5px] font-medium px-2 py-0.5 rounded"
											style={{
												background: "var(--color-green-soft)",
												color: "var(--color-green)",
											}}
										>
											<Check size={10} /> {t("generated", lang)}
										</span>
									) : (
										<button
											type="button"
											onClick={() =>
												setGenerated((s) => {
													const n = new Set(s);
													n.add(idx.name);
													return n;
												})
											}
											className="inline-flex items-center gap-1 text-[10.5px] font-medium px-2 py-0.5 rounded bg-[var(--color-accent)] text-white hover:opacity-90"
										>
											<Plus size={10} /> {t("generateMigration", lang)}
										</button>
									))}
							</div>
						);
					})}
				</div>
			</div>
		</div>
	);
}
