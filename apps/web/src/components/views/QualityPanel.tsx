/** QualityPanel — the agent capability dashboard (质量面板).
 *
 * Polynoia's thesis: the platform harness, not just the model, determines an
 * agent's delivered quality. This panel makes that measurable per CONTACT:
 *
 *   - composite score (0-100): benchmark 45% · tool reliability 25% ·
 *     process reliability 20% · activity 10% (neutral 0.6 where no evidence)
 *   - component bars per agent (turns, tool ok-rate, process ok-rate,
 *     benchmark average)
 *   - the benchmark matrix: case × model executions with scores, so weak
 *     models' progress under the harness is visible over time.
 *
 * Opened from the sidebar header (BarChart3) via store view="quality".
 */
import { ArrowLeft, BarChart3, FlaskConical, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";

type QualityAgent = Awaited<ReturnType<typeof api.quality>>["agents"][number];
type BenchRun = Awaited<ReturnType<typeof api.benchmarkRuns>>["runs"][number];

function scoreColor(score: number): string {
	if (score >= 75) return "var(--color-green)";
	if (score >= 55) return "var(--color-amber, #d9a441)";
	return "var(--color-red)";
}

function Bar({ value, color }: { value: number; color: string }) {
	return (
		<div className="h-1.5 rounded-full bg-[var(--color-line)]/60 overflow-hidden">
			<div
				className="h-full rounded-full transition-all"
				style={{ width: `${Math.round(value * 100)}%`, background: color }}
			/>
		</div>
	);
}

function Metric({
	label,
	display,
	value,
	color,
}: { label: string; display: string; value: number | null; color?: string }) {
	const lang = useStore((s) => s.lang);
	return (
		<div className="space-y-1">
			<div className="flex items-baseline justify-between gap-2">
				<span className="text-[10px] text-[var(--color-fg-3)]">{label}</span>
				<span className="text-[11px] font-medium text-[var(--color-fg-2)] font-mono">
					{display}
				</span>
			</div>
			{value !== null ? (
				<Bar value={value} color={color ?? "var(--color-accent)"} />
			) : (
				<div
					className="h-1.5 rounded-full bg-[var(--color-line)]/30"
					title={t("noData", lang)}
				/>
			)}
		</div>
	);
}

const STATUS_LABEL: Record<string, string> = {
	running: "进行中",
	passed: "通过",
	failed: "未达标",
	error: "异常",
	timeout: "超时",
};

export function QualityPanel() {
	const setView = useStore((s) => s.setView);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const [quality, setQuality] = useState<QualityAgent[] | null>(null);
	const [runs, setRuns] = useState<BenchRun[] | null>(null);
	const [loading, setLoading] = useState(false);

	const load = useCallback(async () => {
		setLoading(true);
		try {
			const [q, b] = await Promise.all([api.quality(), api.benchmarkRuns()]);
			setQuality(q.agents);
			setRuns(b.runs);
		} catch {
			setQuality([]);
			setRuns([]);
		} finally {
			setLoading(false);
		}
	}, []);
	useEffect(() => {
		void load();
	}, [load]);

	const agentOf = (id: string) => agents.find((a) => a.id === id);

	return (
		<div className="flex-1 min-w-0 h-full flex flex-col bg-[var(--color-bg)]">
			<div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]/70 backdrop-blur">
				<button
					type="button"
					onClick={() => setView("chat")}
					className="p-1.5 rounded hover:bg-[var(--color-line)]/50 text-[var(--color-fg-2)]"
					aria-label={t("back", lang)}
				>
					<ArrowLeft size={16} />
				</button>
				<BarChart3 size={16} className="text-[var(--color-accent)]" />
				<h2 className="text-[14px] font-semibold text-[var(--color-fg)]">
					{t("agentQualityPanel", lang)}
				</h2>
				<span className="text-[11px] text-[var(--color-fg-3)]">
					{t("compositeScoreFormula", lang)}
				</span>
				<span className="flex-1" />
				<button
					type="button"
					onClick={() => void load()}
					disabled={loading}
					className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
				>
					<RefreshCw size={12} className={loading ? "animate-spin" : ""} />
					{t("refresh", lang)}
				</button>
			</div>

			<div className="flex-1 overflow-y-auto p-4 space-y-6">
				{/* ── per-agent cards ── */}
				{quality === null ? (
					<div className="text-[12px] text-[var(--color-fg-3)]">
						{t("loading", lang)}
					</div>
				) : quality.length === 0 ? (
					<div className="text-[12px] text-[var(--color-fg-3)]">
						{t("noContacts", lang)}
					</div>
				) : (
					<div
						className="grid gap-3"
						style={{
							gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))",
						}}
					>
						{quality.map((q) => {
							const ag = agentOf(q.agent_id);
							return (
								<div
									key={q.agent_id}
									className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-3.5 space-y-3"
								>
									<div className="flex items-center gap-2.5">
										<span
											className="w-8 h-8 rounded-full grid place-items-center text-[12px] font-bold text-white flex-shrink-0"
											style={{ background: ag?.color ?? "var(--color-fg-4)" }}
										>
											{ag ? (ag.initials || ag.name)[0] : "?"}
										</span>
										<div className="min-w-0 flex-1">
											<div className="text-[13px] font-medium text-[var(--color-fg)] truncate">
												{q.name}
											</div>
											<div className="text-[10px] text-[var(--color-fg-3)] truncate font-mono">
												{ag?.setup?.model ?? ag?.provider ?? ""}
											</div>
										</div>
										<div className="text-right flex-shrink-0">
											<div
												className="text-[20px] font-bold leading-none"
												style={{ color: scoreColor(q.score) }}
											>
												{q.score}
											</div>
											<div className="text-[9px] text-[var(--color-fg-3)]">
												{t("compositeScore", lang)}
											</div>
										</div>
									</div>
									<div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
										<Metric
											label={t("benchmarkAvg", lang)}
											display={
												q.benchmark_avg != null
													? `${Math.round(q.benchmark_avg * 100)}% · ${q.benchmark_runs} 次`
													: t("notRun", lang)
											}
											value={q.benchmark_avg ?? null}
											color={
												q.benchmark_avg != null
													? scoreColor(q.benchmark_avg * 100)
													: undefined
											}
										/>
										<Metric
											label={t("toolReliability", lang)}
											display={
												q.tool_ok_rate != null
													? `${Math.round(q.tool_ok_rate * 100)}% / ${q.tool_calls ?? 0} 调用`
													: t("noDataMetric", lang)
											}
											value={q.tool_ok_rate ?? null}
										/>
										<Metric
											label={t("processReliability", lang)}
											display={
												q.process_ok_rate != null
													? `${Math.round(q.process_ok_rate * 100)}% / ${q.process_runs ?? 0} 进程`
													: t("noDataMetric", lang)
											}
											value={q.process_ok_rate ?? null}
										/>
										<Metric
											label={t("completedTurns", lang)}
											display={
												q.turns
													? `${q.turns} 轮 · 均 ${Math.round(q.avg_turn_seconds ?? 0)}s`
													: t("zeroTurns", lang)
											}
											value={q.turns ? Math.min(1, q.turns / 20) : null}
											color="var(--color-accent)"
										/>
									</div>
								</div>
							);
						})}
					</div>
				)}

				{/* ── benchmark runs ── */}
				<div>
					<div className="flex items-center gap-2 mb-2">
						<FlaskConical size={14} className="text-[var(--color-fg-3)]" />
						<h3 className="text-[12.5px] font-semibold text-[var(--color-fg)]">
							{t("benchmarkRunHistory", lang)}
						</h3>
						<span className="text-[10.5px] text-[var(--color-fg-3)]">
							scripts/testkit/run_benchmark.py --case &lt;key&gt; --model
							&lt;provider/model&gt;
						</span>
					</div>
					{runs === null ? null : runs.length === 0 ? (
						<div className="text-[11.5px] text-[var(--color-fg-3)] border border-dashed border-[var(--color-line)] rounded-lg px-4 py-5">
							{t("noBenchmarkRunsHint", lang)}
						</div>
					) : (
						<div className="rounded-lg border border-[var(--color-line)] overflow-hidden">
							<table className="w-full text-[11.5px]">
								<thead>
									<tr className="bg-[var(--color-surface-2)] text-[var(--color-fg-3)] text-left">
										<th className="px-3 py-1.5 font-medium">
											{t("testcase", lang)}
										</th>
										<th className="px-3 py-1.5 font-medium">
											{t("model", lang)}
										</th>
										<th className="px-3 py-1.5 font-medium">
											{t("status", lang)}
										</th>
										<th className="px-3 py-1.5 font-medium">
											{t("score", lang)}
										</th>
										<th className="px-3 py-1.5 font-medium">
											{t("checks", lang)}
										</th>
										<th className="px-3 py-1.5 font-medium">
											{t("time", lang)}
										</th>
									</tr>
								</thead>
								<tbody>
									{runs.map((r) => (
										<tr
											key={r.id}
											className="border-t border-[var(--color-line)]/60"
										>
											<td className="px-3 py-1.5 text-[var(--color-fg)]">
												{r.case_key}
											</td>
											<td className="px-3 py-1.5 font-mono text-[10.5px] text-[var(--color-fg-2)]">
												{r.model}
											</td>
											<td className="px-3 py-1.5">
												<span
													className="px-1.5 py-0.5 rounded text-[10px]"
													style={{
														color:
															r.status === "passed"
																? "var(--color-green)"
																: r.status === "running"
																	? "var(--color-accent)"
																	: "var(--color-red)",
														background:
															r.status === "passed"
																? "var(--color-green-soft)"
																: r.status === "running"
																	? "var(--color-accent-soft)"
																	: "var(--color-red-soft)",
													}}
												>
													{STATUS_LABEL[r.status] ?? r.status}
												</span>
											</td>
											<td className="px-3 py-1.5 font-mono">
												{r.score != null
													? `${Math.round(r.score * 100)}%`
													: "—"}
											</td>
											<td className="px-3 py-1.5 text-[var(--color-fg-3)]">
												{r.checks.length
													? `${r.checks.filter((c) => c.ok).length}/${r.checks.length} ${t("passed", lang)}`
													: "—"}
											</td>
											<td className="px-3 py-1.5 text-[var(--color-fg-3)]">
												{new Date(r.started_at).toLocaleString("zh-CN", {
													month: "numeric",
													day: "numeric",
													hour: "2-digit",
													minute: "2-digit",
												})}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</div>
			</div>
		</div>
	);
}
