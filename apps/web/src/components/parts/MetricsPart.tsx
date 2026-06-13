import { Activity, ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import { useMemo } from "react";
import { t } from "../../lib/i18n";
import type { MetricsPayload } from "../../lib/types";
import { useStore } from "../../store";

const trendIcon = (t: string) => {
	if (t === "up") return <ArrowUp size={11} />;
	if (t === "down") return <ArrowDown size={11} />;
	return <ArrowRight size={11} />;
};

const colorFor = (c: string | null | undefined) => {
	if (c === "red") return "var(--color-red)";
	if (c === "amber") return "var(--color-amber)";
	if (c === "green") return "var(--color-green)";
	return "var(--color-fg)";
};

export function MetricsPart({ payload }: { payload: MetricsPayload }) {
	const lang = useStore((s) => s.lang);
	// Compute sparkline path + area-fill path
	const { linePath, areaPath, viewWidth, viewHeight } = useMemo(() => {
		const w = 520;
		const h = 56;
		const pad = 6;
		const data = payload.sparkline ?? [];
		if (data.length < 2) {
			return { linePath: "", areaPath: "", viewWidth: w, viewHeight: h };
		}
		const max = Math.max(...data);
		const min = Math.min(...data);
		const range = max - min || 1;
		const stepX = (w - pad * 2) / (data.length - 1);
		const pts = data.map((v, i) => {
			const x = pad + i * stepX;
			const y = h - pad - ((v - min) / range) * (h - pad * 2);
			return `${x.toFixed(1)},${y.toFixed(1)}`;
		});
		const linePath = "M " + pts.join(" L ");
		const areaPath =
			linePath + ` L ${w - pad},${h - pad} L ${pad},${h - pad} Z`;
		return { linePath, areaPath, viewWidth: w, viewHeight: h };
	}, [payload.sparkline]);

	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[600px]">
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<Activity size={14} className="text-[var(--color-fg-3)]" />
				<span className="text-xs font-medium mono truncate flex-1">
					{payload.service}
				</span>
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-medium"
					style={{
						background: "var(--color-red-soft)",
						color: "var(--color-red)",
					}}
				>
					{t("p99Anomaly", lang)}
				</span>
			</div>

			<div className="grid grid-cols-4 px-3 py-3 gap-3">
				{payload.stats.map((s) => (
					<div key={s.label} className="text-center">
						<div className="text-[10px] text-[var(--color-fg-3)] uppercase tracking-wider">
							{s.label}
						</div>
						<div
							className="text-[16px] font-semibold mono mt-0.5"
							style={{ color: colorFor(s.color) }}
						>
							{s.value}
						</div>
						<div
							className="text-[10px] mt-0.5 inline-flex items-center gap-0.5"
							style={{ color: colorFor(s.color) }}
						>
							{trendIcon(s.trend)}
						</div>
					</div>
				))}
			</div>

			<svg
				viewBox={`0 0 ${viewWidth} ${viewHeight}`}
				className="block w-full"
				style={{ height: 70 }}
				preserveAspectRatio="none"
			>
				{areaPath && (
					<path d={areaPath} fill="var(--color-red-soft)" opacity="0.6" />
				)}
				{linePath && (
					<path
						d={linePath}
						fill="none"
						stroke="var(--color-red)"
						strokeWidth="1.5"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
				)}
			</svg>

			<div className="flex items-center gap-2 px-3 py-1.5 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] text-[10.5px] text-[var(--color-fg-3)]">
				<span>{t("lastSixHours", lang)}</span>
				<span className="ml-auto">{t("samplingSourceGrafana", lang)}</span>
			</div>
		</div>
	);
}
