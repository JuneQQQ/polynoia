/** Map a filename to a @git-diff-view language id. Shared by DiffTab +
 * DiffReviewPane so the extension→lang table lives in one place. */
export function inferLang(file: string): string {
	const ext = file.split(".").pop()?.toLowerCase() ?? "";
	switch (ext) {
		case "ts":
		case "tsx":
			return "tsx";
		case "js":
		case "jsx":
			return "jsx";
		case "py":
			return "python";
		case "go":
			return "go";
		case "sql":
			return "sql";
		case "md":
			return "markdown";
		case "html":
		case "htm":
			return "html";
		case "css":
			return "css";
		case "json":
			return "json";
		default:
			return "text";
	}
}
