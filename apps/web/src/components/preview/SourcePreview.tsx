/** SourcePreview — right-rail preview-edit view for source-code files
 * (.py / .ts / .js / .rs / .json / .yaml / ...). The center CodeEditor is the
 * full IDE-style editor; this is the slim variant the right rail renders when
 * the user clicks a code file from the FileTree.
 *
 * Same save semantics as CodeEditor — Ctrl+S / 保存 button → PUT files/raw →
 * server auto-commits on main. Reuses CodeEditor's `langExtForPath` so syntax
 * highlighting matches the center tab. Lighter than CodeEditor: no minimap, no
 * search panel, no 源码/预览 toggle (irrelevant for raw source).
 *
 * Reloads on workspaceFilesTick when not dirty, so an agent rewriting the
 * file shows up live without losing local edits.
 */
import { Prec, type Extension } from "@codemirror/state";
import { type EditorView, keymap } from "@codemirror/view";
import { vscodeKeymap } from "@replit/codemirror-vscode-keymap";
import CodeMirror from "@uiw/react-codemirror";
import { Loader2, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import { langExtForPath } from "./CodeEditor";

export function SourcePreview({
	workspaceId,
	path,
	content,
}: {
	workspaceId: string;
	/** Workspace-relative file path. */
	path: string;
	/** Initial UTF-8 content fetched by RightPreviewFile. */
	content: string;
}) {
	const [value, setValue] = useState(content);
	const [original, setOriginal] = useState(content);
	const [saving, setSaving] = useState(false);
	const viewRef = useRef<EditorView | null>(null);
	const filesTick = useStore((s) => s.workspaceFilesTick);

	const dirty = value !== original;

	// Sync external content updates (initial load or agent rewrite via filesTick)
	// into the editor — but only when the user has no unsaved edits, so we never
	// clobber in-flight typing.
	useEffect(() => {
		setValue((cur) => (cur !== original ? cur : content));
		setOriginal(content);
	}, [content, original]);

	// Agent wrote to main → re-fetch (filesTick bumped by WS handler). Skip if
	// dirty (would lose user edits). Centered here rather than relying on the
	// parent's re-fetch so this component is drop-in anywhere.
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is the reload trigger.
	useEffect(() => {
		if (filesTick === 0) return;
		if (dirty) return;
		let alive = true;
		api
			.workspaceFileRead(workspaceId, path)
			.then((r) => {
				if (!alive) return;
				setValue(r.content);
				setOriginal(r.content);
			})
			.catch(() => {
				/* swallow — parent will render an error card on full load failure */
			});
		return () => {
			alive = false;
		};
	}, [filesTick]);

	const save = useCallback(async () => {
		if (!dirty || saving) return;
		setSaving(true);
		try {
			await api.workspaceFileWrite(workspaceId, path, value);
			setOriginal(value);
		} catch (e) {
			window.alert(`保存失败: ${e}`);
		} finally {
			setSaving(false);
		}
	}, [workspaceId, path, value, dirty, saving]);

	const saveRef = useRef(save);
	useEffect(() => {
		saveRef.current = save;
	}, [save]);

	const extensions = useMemo<Extension[]>(() => {
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
		return exts;
	}, [path]);

	return (
		<div className="flex flex-col h-full min-w-0 bg-[var(--color-surface)]">
			{/* Slim toolbar — just a save button (path already in PreviewPane's
			    header). Keeps vertical space for the actual code. */}
			<div className="flex items-center gap-1 px-2 py-1 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<span className="text-[10px] font-mono text-[var(--color-fg-4)] flex-1">
					{dirty ? "未保存" : "已保存"} · UTF-8
				</span>
				<button
					type="button"
					onClick={save}
					disabled={!dirty || saving}
					className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white disabled:opacity-40 disabled:bg-[var(--color-line)] disabled:text-[var(--color-fg-3)] hover:opacity-90 transition"
					title="保存 (Ctrl+S)"
				>
					{saving ? (
						<Loader2 size={10} className="animate-spin" />
					) : (
						<Save size={10} />
					)}
					{dirty ? "保存" : "已保存"}
				</button>
			</div>
			<div className="flex-1 overflow-hidden">
				<CodeMirror
					value={value}
					extensions={extensions}
					theme="dark"
					onCreateEditor={(view) => {
						viewRef.current = view;
					}}
					onChange={(v) => setValue(v)}
					basicSetup={{
						lineNumbers: true,
						foldGutter: true,
						highlightActiveLine: true,
					}}
					style={{ height: "100%", fontSize: "12.5px" }}
				/>
			</div>
		</div>
	);
}
