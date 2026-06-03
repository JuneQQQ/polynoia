/** HtmlPreview — render a static .html page in a sandboxed iframe.
 *
 * Single-file static pages (inline CSS/JS). Source edits in the code tab live-
 * update here (parent debounces `content`). The toolbar (download) lives in the
 * parent CodeEditor so there's ONE toolbar row, not two stacked.
 */
export function HtmlPreview({ content }: { content: string; fileName?: string }) {
	return (
		<iframe
			title="html-preview"
			// allow-same-origin is REQUIRED for interactive pages: without it the
			// frame runs in an opaque origin where `localStorage` (high scores),
			// IndexedDB, etc. throw SecurityError — the uncaught error halts the
			// page's init script, so canvas games never start their rAF loop and
			// render nothing. allow-scripts+allow-same-origin lets the (locally
			// produced, user-owned) page reach the parent origin; acceptable for
			// this local preview tool. allow-pointer-lock/popups help mouse games.
			sandbox="allow-scripts allow-same-origin allow-pointer-lock allow-popups allow-modals"
			srcDoc={content}
			className="h-full w-full border-0 bg-white"
		/>
	);
}
