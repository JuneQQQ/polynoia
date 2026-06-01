/** SheetPreview — render a CSV/TSV file as an Excel-style table + export.
 *
 * agent writes text (CSV); SheetJS parses it to a grid. Export to native .xlsx,
 * raw .csv, or PDF. (Binary .xlsx display would need a raw-bytes fetch — later;
 * the agent-writes-text path is CSV.)
 */
import { Download } from "lucide-react";
import { useMemo } from "react";
import * as XLSX from "xlsx";
import {
	csvToXlsxBlob,
	downloadBlob,
	downloadText,
	printAsPdf,
} from "./exportUtils";

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
th,td{border:1px solid #ccc;padding:4px 10px;text-align:left;vertical-align:top}
thead th{background:#f3f3f3;font-weight:600}`;

function esc(v: unknown): string {
	return String(v ?? "").replace(
		/[<>&]/g,
		(c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[c] ?? c,
	);
}

function rowsToHtml(rows: Cell[][]): string {
	if (!rows.length) return "<p>(空表格)</p>";
	const [head, ...body] = rows;
	const ncol = head.length;
	return `<table><thead><tr>${head
		.map((c) => `<th>${esc(c)}</th>`)
		.join("")}</tr></thead><tbody>${body
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
	const { rows, error } = useMemo(() => parseRows(content), [content]);
	const base = fileName.replace(/\.[^.]+$/, "");
	const head = rows[0] ?? [];
	const body = rows.slice(1);

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
					label=".csv"
					onClick={() =>
						downloadText(content, `${base}.csv`, "text/csv;charset=utf-8")
					}
				/>
				<ExportBtn
					label="PDF"
					onClick={() =>
						printAsPdf(rowsToHtml(rows), base, `<style>${TABLE_CSS}</style>`)
					}
				/>
			</div>
			<div className="flex-1 overflow-auto p-3">
				{error ? (
					<pre className="text-[var(--color-red)] text-[12px] whitespace-pre-wrap">
						{error}
					</pre>
				) : rows.length === 0 ? (
					<div className="text-[12px] text-[var(--color-fg-3)]">(空表格)</div>
				) : (
					<table className="border-collapse text-[12.5px]">
						<thead>
							<tr>
								{head.map((c, i) => (
									// biome-ignore lint/suspicious/noArrayIndexKey: columns are positional, never reordered
									<th
										key={i}
										className="border border-[var(--color-line)] px-2 py-1 bg-[var(--color-surface-2)] text-left font-semibold whitespace-nowrap"
									>
										{String(c ?? "")}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{body.map((r, ri) => (
								// biome-ignore lint/suspicious/noArrayIndexKey: rows are positional table data, never reordered
								<tr key={ri}>
									{head.map((_, ci) => (
										// biome-ignore lint/suspicious/noArrayIndexKey: cells are positional, never reordered
										<td
											key={ci}
											className="border border-[var(--color-line)] px-2 py-1"
										>
											{String(r[ci] ?? "")}
										</td>
									))}
								</tr>
							))}
						</tbody>
					</table>
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
