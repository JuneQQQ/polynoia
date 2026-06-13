/** SheetPreview — render a CSV/TSV file as an Excel-style sheet + export.
 *
 * agent writes text (CSV); SheetJS parses it to a grid. Export to native .xlsx,
 * or PDF. (Binary .xlsx display would need a raw-bytes fetch — later; the
 * agent-writes-text path is CSV.)
 */
import { Download } from "lucide-react";
import { useMemo } from "react";
import * as XLSX from "xlsx";
import { type Lang, t } from "../../lib/i18n";
import { useStore } from "../../store";
import { csvToXlsxBlob, downloadBlob, printAsPdf } from "./exportUtils";

type Cell = string | number | boolean;

function parseRows(content: string): { rows: Cell[][]; error: string | null } {
	try {
		const wb = XLSX.read(content, { type: "string" });
		const ws = wb.Sheets[wb.SheetNames[0]];
		if (!ws) return { rows: [], error: null };
		const rows = XLSX.utils.sheet_to_json<Cell[]>(ws, {
			header: 1,
			defval: "",
		});
		return { rows, error: null };
	} catch (e) {
		return { rows: [], error: e instanceof Error ? e.message : String(e) };
	}
}

const TABLE_CSS = `table{border-collapse:collapse;font:13px system-ui,sans-serif}
td{border:1px solid #ccc;padding:4px 10px;text-align:left;vertical-align:top}`;

function esc(v: unknown): string {
	return String(v ?? "").replace(
		/[<>&]/g,
		(c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[c] ?? c,
	);
}

function colLabel(index: number): string {
	let n = index + 1;
	let label = "";
	while (n > 0) {
		const rem = (n - 1) % 26;
		label = String.fromCharCode(65 + rem) + label;
		n = Math.floor((n - 1) / 26);
	}
	return label;
}

function rowsToHtml(rows: Cell[][], lang: Lang): string {
	if (!rows.length) return `<p>${t("emptyTable", lang)}</p>`;
	const ncol = Math.max(...rows.map((r) => r.length), 1);
	return `<table><tbody>${rows
		.map(
			(r) =>
				`<tr>${Array.from({ length: ncol }, (_, i) => `<td>${esc(r[i])}</td>`).join("")}</tr>`,
		)
		.join("")}</tbody></table>`;
}

export function SheetPreview({
	content,
	fileName,
}: { content: string; fileName: string }) {
	const lang = useStore((s) => s.lang);
	const { rows, error } = useMemo(() => parseRows(content), [content]);
	const base = fileName.replace(/\.[^.]+$/, "");
	const colCount = Math.max(...rows.map((r) => r.length), 1);
	const colIndexes = Array.from({ length: colCount }, (_, i) => i);

	return (
		<div className="h-full flex flex-col bg-white">
			<div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
				<span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">
					{fileName}
				</span>
				<ExportBtn
					label=".xlsx"
					onClick={() => downloadBlob(csvToXlsxBlob(content), `${base}.xlsx`)}
				/>
				<ExportBtn
					label="PDF"
					onClick={() =>
						printAsPdf(
							rowsToHtml(rows, lang),
							base,
							`<style>${TABLE_CSS}</style>`,
						)
					}
				/>
			</div>
			<div className="flex-1 overflow-auto p-3">
				{error ? (
					<pre className="text-[var(--color-red)] text-[12px] whitespace-pre-wrap">
						{error}
					</pre>
				) : rows.length === 0 ? (
					<div className="text-[12px] text-[var(--color-fg-3)]">
						{t("emptyTable", lang)}
					</div>
				) : (
					<div className="inline-block min-w-full border border-[#d6dbe3] bg-white font-mono text-[12px] leading-normal text-[#111827] shadow-sm">
						<div className="sticky top-0 z-20 flex bg-[#f3f6fb] text-[#4b5563]">
							<div className="sticky left-0 z-30 h-7 w-12 flex-shrink-0 border-r border-b border-[#d6dbe3] bg-[#eef2f7]" />
							{colIndexes.map((ci) => (
								<div
									key={ci}
									className="h-7 w-28 flex-shrink-0 border-r border-b border-[#d6dbe3] px-2 py-1 text-center font-medium"
								>
									{colLabel(ci)}
								</div>
							))}
						</div>
						{rows.map((r, ri) => (
							// biome-ignore lint/suspicious/noArrayIndexKey: rows are positional spreadsheet cells
							<div key={ri} className="flex">
								<div className="sticky left-0 z-10 h-8 w-12 flex-shrink-0 border-r border-b border-[#d6dbe3] bg-[#f3f6fb] px-2 py-1.5 text-right text-[#4b5563]">
									{ri + 1}
								</div>
								{colIndexes.map((ci) => (
									<div
										key={ci}
										className="h-8 w-28 flex-shrink-0 overflow-hidden text-ellipsis whitespace-nowrap border-r border-b border-[#e2e8f0] bg-white px-2 py-1.5"
										title={String(r[ci] ?? "")}
									>
										{String(r[ci] ?? "")}
									</div>
								))}
							</div>
						))}
						<div className="sticky bottom-0 z-20 flex h-8 items-center border-t border-[#d6dbe3] bg-[#f8fafc] px-3 text-[11px] text-[#4b5563]">
							<div className="border-t-2 border-[#217346] bg-white px-4 py-1 font-ui text-[#217346]">
								Sheet1
							</div>
						</div>
					</div>
				)}
			</div>
		</div>
	);
}

function ExportBtn({ label, onClick }: { label: string; onClick: () => void }) {
	return (
		<button
			type="button"
			onClick={onClick}
			className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
		>
			<Download size={11} /> {label}
		</button>
	);
}
