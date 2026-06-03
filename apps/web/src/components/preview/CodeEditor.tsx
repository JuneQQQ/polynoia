/** CodeEditor — a single workspace file open as a CENTER tab (Phase 2).
 *
 * One file: load → edit (CodeMirror, dark theme + per-language syntax
 * highlighting) → Ctrl+S / 保存 writes back via PUT + auto-commits on main.
 * Auto-reloads (when clean) when an agent writes files to main
 * (workspaceFilesTick). Split out of the old monolithic CodeTab so the editor
 * can live in the center next to the chat while the file tree stays on the right.
 */
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { rust } from "@codemirror/lang-rust";
import { sql } from "@codemirror/lang-sql";
import { yaml } from "@codemirror/lang-yaml";
import { openSearchPanel } from "@codemirror/search";
import { type Extension, Prec } from "@codemirror/state";
import { type EditorView, keymap } from "@codemirror/view";
import { showMinimap } from "@replit/codemirror-minimap";
import { vscodeKeymap } from "@replit/codemirror-vscode-keymap";
import CodeMirror from "@uiw/react-codemirror";
import {
	Eye,
	Loader2,
	Map as MapIcon,
	Pencil,
	Save,
	Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import { DocPreviewPane, docKind } from "./DocPreviewPane";

const MINIMAP_EXT: Extension = showMinimap.compute([], () => ({
	create: () => ({ dom: document.createElement("div") }),
	displayText: "blocks",
	showOverlay: "always",
}));

function extOf(path: string): string {
	const m = path.match(/\.([a-zA-Z0-9]+)$/);
	return m ? m[1].toLowerCase() : "";
}

/** Map a file extension → a CodeMirror language extension (for highlighting).
 * Off-the-shelf @codemirror/lang-* packages; unknown extensions fall back to
 * no language (plain text, still editable). */
function langExtForPath(path: string): Extension | undefined {
	switch (extOf(path)) {
		case "ts":
		case "tsx":
			return javascript({ jsx: true, typescript: true });
		case "js":
		case "jsx":
		case "mjs":
		case "cjs":
			return javascript({ jsx: true });
		case "py":
		case "pyi":
			return python();
		case "html":
		case "htm":
			return html();
		case "css":
		case "scss":
		case "less":
			return css();
		case "json":
			return json();
		case "md":
		case "mdx":
			return markdown();
		case "rs":
			return rust();
		case "yaml":
		case "yml":
			return yaml();
		case "sql":
			return sql();
		default:
			return undefined;
	}
}

export function CodeEditor({
	workspaceId,
	path,
}: {
	workspaceId: string;
	path: string;
}) {
	const [content, setContent] = useState<string | null>(null);
	const [original, setOriginal] = useState("");
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [minimapOn, setMinimapOn] = useState(
		() => localStorage.getItem("polynoia:code-minimap") !== "0",
	);
	useEffect(() => {
		localStorage.setItem("polynoia:code-minimap", minimapOn ? "1" : "0");
	}, [minimapOn]);
	const cmViewRef = useRef<EditorView | null>(null);
	const filesTick = useStore((s) => s.workspaceFilesTick);

	// Doc-type files (.md/.xlsx/.html/Marp) get a rendered-preview toggle —
	// WYSIWYG doc (Crepe), embedded workbook, Marp slides, or HTML iframe. CSV is
	// intentionally plain text; it should not masquerade as a native workbook.
	const kind = docKind(path, content ?? "");
	const isDoc = kind !== "other";
	const isWorkbook = kind === "workbook";
	// Binary office docs (docx/pptx/xlsx) have NO meaningful text source — opening
	// them in CodeMirror shows garbled bytes + saving would corrupt the file. They
	// are preview-ONLY (read-only): no 源码 toggle, no save, and we skip the text
	// read entirely (OfficePreview/WorkbookPreview fetch the bytes themselves).
	const isBinary =
		kind === "word" || kind === "slides" || kind === "workbook";
	const [preview, setPreview] = useState(() => docKind(path, "") !== "other");

	const dirty = content !== null && content !== original;

	// Load (and reload on workspaceFilesTick, but only when not dirty so we never
	// clobber unsaved edits — an agent wrote files to main).
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger; content/original are read via functional setState, so listing them would re-fetch on every keystroke.
	useEffect(() => {
		// Binary docs: don't text-read (it returns garbled bytes); the binary
		// previewers load the bytes themselves.
		const k0 = docKind(path, "");
		if (k0 === "workbook" || k0 === "word" || k0 === "slides") {
			setContent("");
			setOriginal("");
			setLoading(false);
			return;
		}
		let alive = true;
		setLoading(content === null);
		api
			.workspaceFileRead(workspaceId, path)
			.then(({ content: c }) => {
				if (!alive) return;
				setContent((cur) => (cur !== null && cur !== original ? cur : c));
				setOriginal(c);
				setLoading(false);
			})
			.catch((e) => {
				if (!alive) return;
				setContent(`// failed to load: ${e}`);
				setOriginal("");
				setLoading(false);
			});
		return () => {
			alive = false;
		};
	}, [workspaceId, path, filesTick]);

	const save = useCallback(async () => {
		if (content === null || content === original || saving) return;
		setSaving(true);
		try {
			await api.workspaceFileWrite(workspaceId, path, content);
			setOriginal(content);
		} catch (e) {
			window.alert(`保存失败: ${e}`);
		} finally {
			setSaving(false);
		}
	}, [workspaceId, path, content, original, saving]);

	const saveRef = useRef(save);
	useEffect(() => {
		saveRef.current = save;
	}, [save]);

	const editorExtensions = useMemo<Extension[]>(() => {
		const exts: Extension[] = [
			Prec.highest(
				keymap.of([
					{
						key: "Mod-s",
						preventDefault: true,
						run: () => {
							saveRef.current();
							return true;
						},
					},
				]),
			),
			Prec.high(keymap.of(vscodeKeymap)),
		];
		const lang = langExtForPath(path);
		if (lang) exts.push(lang);
		if (minimapOn) exts.push(MINIMAP_EXT);
		return exts;
	}, [path, minimapOn]);

	return (
		<div className="flex flex-col h-full min-w-0 bg-[var(--color-surface)]">
			<div className="flex items-center gap-1 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<span className="text-[11px] mono text-[var(--color-fg-3)] truncate flex-1">
					{path}
				</span>
				{isBinary && (
					<span className="text-[10px] font-mono text-[var(--color-fg-4)] px-1.5 py-0.5 rounded bg-[var(--color-surface)]">
						只读预览
					</span>
				)}
				{isDoc && !isBinary && (
					<button
						type="button"
						onClick={() => setPreview((v) => !v)}
						aria-pressed={preview}
						className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] hover:bg-[var(--color-surface)] ${
							preview
								? "text-[var(--color-accent)]"
								: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
						}`}
						title={preview ? "查看 / 编辑源码" : "预览(可导出)"}
					>
						{preview ? <Pencil size={12} /> : <Eye size={12} />}
						{preview ? "源码" : "预览"}
					</button>
				)}
				{!preview && (
					<>
						<button
							type="button"
							onClick={() => {
								const v = cmViewRef.current;
								if (v) {
									v.focus();
									openSearchPanel(v);
								}
							}}
							className="p-1.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-surface)]"
							title="查找 / 替换 (Ctrl+F)"
						>
							<Search size={12} />
						</button>
						<button
							type="button"
							onClick={() => setMinimapOn((v) => !v)}
							aria-pressed={minimapOn}
							className={`p-1.5 rounded hover:bg-[var(--color-surface)] ${
								minimapOn
									? "text-[var(--color-accent)]"
									: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
							}`}
							title={minimapOn ? "隐藏小地图" : "显示小地图"}
						>
							<MapIcon size={12} />
						</button>
					</>
				)}
				{!isBinary && (
					<button
						type="button"
						onClick={save}
						disabled={!dirty || saving}
						className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white disabled:opacity-40 disabled:bg-[var(--color-line)] disabled:text-[var(--color-fg-3)] hover:opacity-90 transition"
						title="保存 (Ctrl+S)"
					>
						{saving ? (
							<Loader2 size={11} className="animate-spin" />
						) : (
							<Save size={11} />
						)}
						{dirty ? "保存" : "已保存"}
					</button>
				)}
			</div>
			<div className="flex-1 overflow-hidden">
				{loading ? (
					<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
						<Loader2 size={14} className="animate-spin" />
					</div>
				) : isWorkbook ? (
					<DocPreviewPane workspaceId={workspaceId} path={path} content="" />
				) : isBinary || (isDoc && preview) ? (
					<DocPreviewPane
						workspaceId={workspaceId}
						path={path}
						content={content ?? ""}
					/>
				) : (
					<CodeMirror
						value={content ?? ""}
						extensions={editorExtensions}
						theme="dark"
						onCreateEditor={(view) => {
							cmViewRef.current = view;
						}}
						onChange={(val) => setContent(val)}
						basicSetup={{
							lineNumbers: true,
							foldGutter: true,
							highlightActiveLine: true,
						}}
						style={{ height: "100%", fontSize: "12.5px" }}
					/>
				)}
			</div>
			<footer className="flex items-center gap-3 px-3 py-1 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] text-[10.5px] text-[var(--color-fg-3)] mono">
				<span className="truncate flex-1">{path}</span>
				<span>{extOf(path).toUpperCase() || "TEXT"}</span>
				<span>{isWorkbook ? "BINARY" : "UTF-8"}</span>
				{!isWorkbook && <span>{(content ?? "").split("\n").length} 行</span>}
			</footer>
		</div>
	);
}
