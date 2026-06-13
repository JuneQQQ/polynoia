import { Sparkles } from "lucide-react";
import { useState } from "react";
import type { SwatchesPayload } from "../../lib/types";

export function SwatchesPart({ payload }: { payload: SwatchesPayload }) {
	const [copied, setCopied] = useState<number | null>(null);
	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[480px]">
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<Sparkles size={14} className="text-[var(--color-fg-3)]" />
				<span className="text-xs font-medium mono">brand.tokens.json</span>
				<span className="ml-auto text-[10.5px] uppercase tracking-wide text-[var(--color-fg-3)]">
					design
				</span>
			</div>
			<div className="flex p-3 gap-2">
				{payload.swatches.map((s, i) => (
					<button
						key={i}
						type="button"
						onClick={() => {
							navigator.clipboard.writeText(s.hex);
							setCopied(i);
							setTimeout(() => setCopied(null), 1200);
						}}
						className="flex-1 min-w-0 text-left"
					>
						<div
							className="h-14 rounded-md transition-transform"
							style={{
								background: s.hex,
								border:
									copied === i
										? "2px solid var(--color-accent)"
										: "1px solid var(--color-line)",
								transform: copied === i ? "scale(0.96)" : "scale(1)",
							}}
						/>
						<div className="mt-1.5 text-[11px] text-[var(--color-fg-3)]">
							{s.name}
						</div>
						<div className="text-[10.5px] text-[var(--color-fg-4)] mono">
							{s.hex}
						</div>
					</button>
				))}
			</div>
		</div>
	);
}
