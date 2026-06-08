/** HtmlPreview — render a static .html page in a sandboxed iframe.
 *
 * Single-file static pages (inline CSS/JS). Source edits in the code tab live-
 * update here (parent debounces `content`). The toolbar (download) lives in the
 * parent CodeEditor so there's ONE toolbar row, not two stacked.
 */

// Click-guard injected into the previewed page. With `allow-same-origin`, a
// `srcDoc` frame's base URL is the PARENT origin (127.0.0.1:517x), so a link
// inside the page — `<a href="#day2">` tabs, `<a href="/">`, a relative href —
// would navigate the IFRAME to the app origin (loading Polynoia inside the frame
// → "要求重新连接服务器") or reload the srcdoc on a hash change ("按钮点击乱跳").
// This keeps in-page anchors as a smooth in-frame scroll and stops every other
// link from navigating the frame (external ones open in a new tab instead). It
// runs in the CAPTURE phase so the page's own onclick handlers (tab toggles,
// etc.) still fire — only the unwanted default navigation is suppressed.
const NAV_GUARD = `<script>(function(){
  document.addEventListener("click",function(e){
    var a=e.target&&e.target.closest?e.target.closest("a[href]"):null;
    if(!a)return;
    var h=a.getAttribute("href")||"";
    if(h.charAt(0)==="#"){
      e.preventDefault();
      var id=decodeURIComponent(h.slice(1));
      if(!id)return;
      var t=document.getElementById(id)||document.querySelector('[name="'+id.replace(/"/g,'\\\\"')+'"]');
      if(t)t.scrollIntoView({behavior:"smooth",block:"start"});
    }else if(h&&h.indexOf("javascript:")!==0){
      e.preventDefault();
      if(/^https?:\\/\\//i.test(a.href)){try{window.open(a.href,"_blank","noopener")}catch(_){}}
    }
  },true);
})();</script>`;

function withNavGuard(html: string): string {
	// Inject before </body> (keeps the doctype first → no quirks mode); append as
	// a fallback for fragments without a body tag.
	return html.includes("</body>")
		? html.replace("</body>", `${NAV_GUARD}</body>`)
		: html + NAV_GUARD;
}

export function HtmlPreview({
	content,
}: { content: string; fileName?: string }) {
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
			// NO allow-top-navigation: the page can't navigate the app away. The
			// injected NAV_GUARD additionally stops in-FRAME link navigation (which
			// would otherwise load the app into the frame / reload the srcdoc).
			sandbox="allow-scripts allow-same-origin allow-pointer-lock allow-popups allow-modals"
			srcDoc={withNavGuard(content)}
			className="h-full w-full border-0 bg-white"
		/>
	);
}
