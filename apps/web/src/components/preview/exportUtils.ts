/** Shared download/export helpers for the doc/PPT/sheet previews.
 *
 * PDF export uses the browser's native print-to-PDF via a hidden iframe (zero
 * extra deps, faithful rendering — the user picks "Save as PDF" in the dialog).
 * Native .xlsx uses SheetJS. .docx/.pptx native export are a later step.
 */
import * as XLSX from "xlsx";

export function downloadBlob(blob: Blob, filename: string): void {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	a.remove();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadText(
	text: string,
	filename: string,
	mime = "text/plain;charset=utf-8",
): void {
	downloadBlob(new Blob([text], { type: mime }), filename);
}

/** Write a COMPLETE html document into a hidden iframe and open the print
 * dialog → "Save as PDF". Use this when you already have a full <html> doc
 * (e.g. a static .html page) so its own <head>/<style> are preserved. */
export function printHtmlDoc(fullHtml: string): void {
	const iframe = document.createElement("iframe");
	iframe.setAttribute("aria-hidden", "true");
	Object.assign(iframe.style, {
		position: "fixed",
		right: "0",
		bottom: "0",
		width: "0",
		height: "0",
		border: "0",
	});
	document.body.appendChild(iframe);
	const doc = iframe.contentDocument;
	const win = iframe.contentWindow;
	if (!doc || !win) {
		iframe.remove();
		return;
	}
	doc.open();
	doc.write(fullHtml);
	doc.close();
	const cleanup = () => setTimeout(() => iframe.remove(), 1500);
	win.addEventListener("afterprint", cleanup);
	setTimeout(() => {
		win.focus();
		win.print();
		cleanup();
	}, 300);
}

/** Wrap a body fragment in a minimal doc (`headExtra` can carry <style>) and
 * print → PDF. For full documents use `printHtmlDoc`. */
export function printAsPdf(
	bodyHtml: string,
	title = "导出",
	headExtra = "",
): void {
	printHtmlDoc(
		`<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>${headExtra}</head><body>${bodyHtml}</body></html>`,
	);
}

/** CSV text → native .xlsx Blob (SheetJS). */
export function csvToXlsxBlob(csv: string): Blob {
	const wb = XLSX.read(csv, { type: "string" });
	const out = XLSX.write(wb, {
		type: "array",
		bookType: "xlsx",
	}) as ArrayBuffer;
	return new Blob([out], {
		type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	});
}
