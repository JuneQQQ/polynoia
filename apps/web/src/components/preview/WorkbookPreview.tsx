/** WorkbookPreview — binary .xlsx editor embedded in the preview surface.
 *
 * CSV remains plain text. This component opens real .xlsx bytes, renders an
 * editable grid, and writes native .xlsx bytes back to the workspace.
 */
import {
	Check,
	Download,
	FileSpreadsheet,
	Loader2,
	Plus,
	Save,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as XLSX from "xlsx";
import { api } from "../../lib/api";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";
import { downloadBlob } from "./exportUtils";

type CellValue = string | number | boolean | Date | null;
type SheetGrid = { name: string; rows: CellValue[][] };
type CellPos = { row: number; col: number };

const XLSX_MIME =
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const MIN_ROWS = 30;
const MIN_COLS = 12;

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

function cellLabel(pos: CellPos): string {
	return `${colLabel(pos.col)}${pos.row + 1}`;
}

function cellKey(row: number, col: number): string {
	return `${row}:${col}`;
}

function displayValue(value: CellValue | undefined): string {
	if (value instanceof Date) return value.toISOString().slice(0, 10);
	return String(value ?? "");
}

function workbookToSheets(workbook: XLSX.WorkBook): SheetGrid[] {
	const sheets = workbook.SheetNames.map((name) => {
		const ws = workbook.Sheets[name];
		const rows = ws
			? XLSX.utils.sheet_to_json<CellValue[]>(ws, { header: 1, defval: "" })
			: [];
		return { name, rows };
	});
	return sheets.length ? sheets : [{ name: "Sheet1", rows: [] }];
}

function isBlankCell(value: CellValue | undefined): boolean {
	return value === null || value === undefined || String(value) === "";
}

function trimRows(rows: CellValue[][]): CellValue[][] {
	let lastRow = rows.length - 1;
	while (lastRow >= 0 && rows[lastRow].every(isBlankCell)) lastRow -= 1;
	return rows.slice(0, lastRow + 1).map((row) => {
		let lastCol = row.length - 1;
		while (lastCol >= 0 && isBlankCell(row[lastCol])) lastCol -= 1;
		return row.slice(0, lastCol + 1);
	});
}

function uniqueSheetName(sheets: SheetGrid[]): string {
	const names = new Set(sheets.map((s) => s.name));
	for (let i = sheets.length + 1; i < 1000; i += 1) {
		const name = `Sheet${i}`;
		if (!names.has(name)) return name;
	}
	return `Sheet${Date.now()}`;
}

function safeSheetName(name: string, index: number, used: Set<string>): string {
	const base =
		name
			.replace(/[:\\/?*\[\]]/g, " ")
			.trim()
			.slice(0, 31) || `Sheet${index + 1}`;
	let candidate = base;
	let suffix = 2;
	while (used.has(candidate)) {
		const tail = ` ${suffix}`;
		candidate = `${base.slice(0, Math.max(1, 31 - tail.length))}${tail}`;
		suffix += 1;
	}
	used.add(candidate);
	return candidate;
}

function sheetsToBlob(sheets: SheetGrid[]): Blob {
	const workbook = XLSX.utils.book_new();
	const used = new Set<string>();
	for (const [index, sheet] of sheets.entries()) {
		const ws = XLSX.utils.aoa_to_sheet(trimRows(sheet.rows));
		XLSX.utils.book_append_sheet(
			workbook,
			ws,
			safeSheetName(sheet.name, index, used),
		);
	}
	const out = XLSX.write(workbook, {
		bookType: "xlsx",
		type: "array",
	}) as ArrayBuffer;
	return new Blob([out], { type: XLSX_MIME });
}

function maxCols(rows: CellValue[][]): number {
	return rows.reduce((max, row) => Math.max(max, row.length), 0);
}

function updateCell(
	sheets: SheetGrid[],
	activeSheet: number,
	row: number,
	col: number,
	value: string,
): SheetGrid[] {
	return sheets.map((sheet, index) => {
		if (index !== activeSheet) return sheet;
		const rows = sheet.rows.map((r) => [...r]);
		while (rows.length <= row) rows.push([]);
		const nextRow = [...(rows[row] ?? [])];
		while (nextRow.length <= col) nextRow.push("");
		nextRow[col] = value;
		rows[row] = nextRow;
		return { ...sheet, rows };
	});
}

export function WorkbookPreview({
	workspaceId,
	path,
	fileName,
}: {
	workspaceId: string;
	path: string;
	fileName: string;
}) {
	const [sheets, setSheets] = useState<SheetGrid[]>([]);
	const [activeSheet, setActiveSheet] = useState(0);
	const [activeCell, setActiveCell] = useState<CellPos>({ row: 0, col: 0 });
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [dirty, setDirty] = useState(false);
	const [saving, setSaving] = useState(false);
	const dirtyRef = useRef(false);
	const inputRefs = useRef(new Map<string, HTMLInputElement>());
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const lang = useStore((s) => s.lang);

	useEffect(() => {
		dirtyRef.current = dirty;
	}, [dirty]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger; dirtyRef prevents clobbering local edits.
	useEffect(() => {
		let alive = true;
		if (dirtyRef.current) return;
		setLoading(true);
		setError(null);
		api
			.workspaceFileBytesRead(workspaceId, path)
			.then(({ data }) => {
				if (!alive) return;
				const workbook = XLSX.read(data, { type: "array", cellDates: true });
				setSheets(workbookToSheets(workbook));
				setActiveSheet(0);
				setActiveCell({ row: 0, col: 0 });
				setDirty(false);
				setLoading(false);
			})
			.catch((e) => {
				if (!alive) return;
				setError(e instanceof Error ? e.message : String(e));
				setLoading(false);
			});
		return () => {
			alive = false;
		};
	}, [workspaceId, path, filesTick]);

	const sheet = sheets[activeSheet] ??
		sheets[0] ?? { name: "Sheet1", rows: [] };
	const colCount = Math.max(MIN_COLS, maxCols(sheet.rows) + 2);
	const rowCount = Math.max(MIN_ROWS, sheet.rows.length + 2);
	const colIndexes = useMemo(
		() => Array.from({ length: colCount }, (_, i) => i),
		[colCount],
	);
	const rowIndexes = useMemo(
		() => Array.from({ length: rowCount }, (_, i) => i),
		[rowCount],
	);
	const activeInputValue = displayValue(
		sheet.rows[activeCell.row]?.[activeCell.col],
	);

	const focusCell = useCallback((row: number, col: number) => {
		const next = { row: Math.max(0, row), col: Math.max(0, col) };
		setActiveCell(next);
		window.requestAnimationFrame(() => {
			inputRefs.current.get(cellKey(next.row, next.col))?.focus();
		});
	}, []);

	const changeCell = useCallback(
		(row: number, col: number, value: string) => {
			setSheets((cur) => updateCell(cur, activeSheet, row, col, value));
			setDirty(true);
		},
		[activeSheet],
	);

	const save = useCallback(async () => {
		if (!dirty || saving) return;
		setSaving(true);
		try {
			await api.workspaceFileBytesWrite(
				workspaceId,
				path,
				sheetsToBlob(sheets.length ? sheets : [{ name: "Sheet1", rows: [] }]),
			);
			setDirty(false);
			useStore.getState().bumpWorkspaceFiles();
		} catch (e) {
			window.alert(`${t("saveFailed", lang)}${e}`);
		} finally {
			setSaving(false);
		}
	}, [workspaceId, path, sheets, dirty, saving, lang]);

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
				e.preventDefault();
				void save();
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [save]);

	const exportWorkbook = useCallback(() => {
		downloadBlob(
			sheetsToBlob(sheets.length ? sheets : [{ name: "Sheet1", rows: [] }]),
			fileName.endsWith(".xlsx") ? fileName : `${fileName}.xlsx`,
		);
	}, [fileName, sheets]);

	const addSheet = useCallback(() => {
		const name = uniqueSheetName(sheets);
		setSheets([...sheets, { name, rows: [] }]);
		setActiveSheet(sheets.length);
		setActiveCell({ row: 0, col: 0 });
		setDirty(true);
	}, [sheets]);

	const onCellKeyDown = (
		e: React.KeyboardEvent<HTMLInputElement>,
		row: number,
		col: number,
	) => {
		if (e.key === "Enter") {
			e.preventDefault();
			focusCell(row + 1, col);
			return;
		}
		if (e.key === "Tab") {
			e.preventDefault();
			focusCell(row, col + (e.shiftKey ? -1 : 1));
			return;
		}
		if (e.key === "ArrowDown") {
			e.preventDefault();
			focusCell(row + 1, col);
			return;
		}
		if (e.key === "ArrowUp") {
			e.preventDefault();
			focusCell(row - 1, col);
			return;
		}
		const target = e.currentTarget;
		if (
			e.key === "ArrowLeft" &&
			target.selectionStart === 0 &&
			target.selectionEnd === 0
		) {
			e.preventDefault();
			focusCell(row, col - 1);
		}
		if (
			e.key === "ArrowRight" &&
			target.selectionStart === target.value.length &&
			target.selectionEnd === target.value.length
		) {
			e.preventDefault();
			focusCell(row, col + 1);
		}
	};

	return (
		<div className="h-full flex flex-col bg-white text-[#111827]">
			<div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
				<FileSpreadsheet
					size={12}
					className="text-[var(--color-green)] flex-shrink-0"
				/>
				<span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">
					{path}
				</span>
				{dirty && (
					<span
						className="w-1.5 h-1.5 rounded-full"
						style={{ background: "var(--color-amber)" }}
						title={t("unsavedChanges", lang)}
					/>
				)}
				<ToolbarButton
					onClick={save}
					disabled={!dirty || saving}
					title={t("saveTitle", lang)}
					primary
				>
					{saving ? (
						<Loader2 size={11} className="animate-spin" />
					) : dirty ? (
						<Save size={11} />
					) : (
						<Check size={11} />
					)}
					{dirty ? t("save", lang) : t("saved", lang)}
				</ToolbarButton>
				<ToolbarButton onClick={exportWorkbook} title={t("downloadXlsx", lang)}>
					<Download size={11} />
					.xlsx
				</ToolbarButton>
			</div>

			{loading ? (
				<div className="grid flex-1 place-items-center text-[12px] text-[var(--color-fg-3)]">
					<Loader2 size={14} className="animate-spin" />
				</div>
			) : error ? (
				<pre className="m-0 flex-1 overflow-auto p-3 text-[12px] text-[var(--color-red)] whitespace-pre-wrap">
					{error}
				</pre>
			) : (
				<>
					<div className="flex h-8 flex-shrink-0 items-center border-b border-[#d6dbe3] bg-[#f8fafc] text-[12px]">
						<div className="h-full w-20 border-r border-[#d6dbe3] px-2 py-1.5 font-mono text-[#4b5563]">
							{cellLabel(activeCell)}
						</div>
						<div className="h-full w-10 border-r border-[#d6dbe3] px-2 py-1.5 text-center italic text-[#6b7280]">
							fx
						</div>
						<input
							value={activeInputValue}
							onChange={(e) =>
								changeCell(activeCell.row, activeCell.col, e.target.value)
							}
							className="h-full min-w-0 flex-1 bg-white px-2 font-mono text-[#111827] outline-none"
						/>
					</div>
					<div className="flex-1 min-h-0 overflow-auto bg-[#f8fafc]">
						<div className="inline-block min-w-full bg-white font-mono text-[12px] leading-normal">
							<div className="sticky top-0 z-20 flex bg-[#f3f6fb] text-[#4b5563]">
								<div className="sticky left-0 z-30 h-7 w-12 flex-shrink-0 border-r border-b border-[#d6dbe3] bg-[#eef2f7]" />
								{colIndexes.map((ci) => (
									<div
										key={ci}
										className="h-7 w-32 flex-shrink-0 border-r border-b border-[#d6dbe3] px-2 py-1 text-center font-medium"
									>
										{colLabel(ci)}
									</div>
								))}
							</div>
							{rowIndexes.map((ri) => (
								<div key={ri} className="flex">
									<div className="sticky left-0 z-10 h-8 w-12 flex-shrink-0 border-r border-b border-[#d6dbe3] bg-[#f3f6fb] px-2 py-1.5 text-right text-[#4b5563]">
										{ri + 1}
									</div>
									{colIndexes.map((ci) => {
										const selected =
											activeCell.row === ri && activeCell.col === ci;
										return (
											<input
												key={ci}
												ref={(node) => {
													const key = cellKey(ri, ci);
													if (node) inputRefs.current.set(key, node);
													else inputRefs.current.delete(key);
												}}
												value={displayValue(sheet.rows[ri]?.[ci])}
												onFocus={() => setActiveCell({ row: ri, col: ci })}
												onChange={(e) => changeCell(ri, ci, e.target.value)}
												onKeyDown={(e) => onCellKeyDown(e, ri, ci)}
												title={displayValue(sheet.rows[ri]?.[ci])}
												className={`h-8 w-32 flex-shrink-0 border-r border-b border-[#e2e8f0] bg-white px-2 py-1 text-[#111827] outline-none ${
													selected
														? "relative z-10 ring-2 ring-[#217346] ring-inset"
														: "focus:ring-1 focus:ring-[#9ca3af] focus:ring-inset"
												}`}
											/>
										);
									})}
								</div>
							))}
						</div>
					</div>
					<div className="flex h-9 flex-shrink-0 items-end gap-1 border-t border-[#d6dbe3] bg-[#f8fafc] px-3 text-[11px] text-[#4b5563]">
						{sheets.map((s, index) => (
							<button
								key={s.name}
								type="button"
								onClick={() => {
									setActiveSheet(index);
									setActiveCell({ row: 0, col: 0 });
								}}
								className={`h-8 px-4 font-ui ${
									index === activeSheet
										? "border-t-2 border-[#217346] bg-white text-[#217346]"
										: "border border-transparent text-[#4b5563] hover:bg-white"
								}`}
							>
								{s.name}
							</button>
						))}
						<button
							type="button"
							onClick={addSheet}
							title={t("addNewSheet", lang)}
							className="mb-1 grid h-6 w-6 place-items-center rounded border border-[#d6dbe3] bg-white text-[#4b5563] hover:border-[#217346] hover:text-[#217346]"
						>
							<Plus size={12} />
						</button>
					</div>
				</>
			)}
		</div>
	);
}

function ToolbarButton({
	children,
	onClick,
	disabled,
	title,
	primary,
}: {
	children: React.ReactNode;
	onClick: () => void;
	disabled?: boolean;
	title: string;
	primary?: boolean;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			disabled={disabled}
			title={title}
			className={
				primary
					? "inline-flex items-center gap-1 px-2 py-0.5 rounded font-medium bg-[var(--color-accent)] text-white disabled:opacity-40 disabled:bg-[var(--color-line)] disabled:text-[var(--color-fg-3)] hover:opacity-90 transition flex-shrink-0"
					: "inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
			}
		>
			{children}
		</button>
	);
}
