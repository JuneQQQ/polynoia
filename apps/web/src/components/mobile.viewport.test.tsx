/**
 * mobile.viewport.test.tsx — MOBILE-VIEWPORT rendering of the lightweight-IM
 * subset (Capacitor build, isMobile()===true).
 *
 * GAP THIS CLOSES: every other component test in this repo renders the DESKTOP
 * path (the UA/viewport heuristic resolves to "browser" under vitest, so
 * isMobile() is false). The Capacitor mobile branches — which gate real,
 * user-visible affordances — were entirely untested. We force the mobile path
 * by vi.mock()'ing ../lib/platform so isMobile() === true, then assert the
 * mobile-only rendering contract of each part that branches on it.
 *
 * RENDER HARNESS — renderToStaticMarkup, deliberately:
 *   jsdom is NOT installed in apps/web (see package.json devDependencies — no
 *   jsdom / happy-dom / @testing-library). Every existing component test
 *   (BrandIcon / ConnectionBanner / ErrorPart / TextPart.cjkMarkdown) renders
 *   with react-dom/server's renderToStaticMarkup, which is jsdom-free and is the
 *   established pattern here. We mirror it. The prompt asked for "vitest +
 *   jsdom"; that exact environment cannot be provisioned under the read-only /
 *   add-one-file constraint, so we use the in-repo jsdom-free equivalent. Note
 *   this means we assert on the SERVER-RENDERED markup (the className the mobile
 *   branch emits), which is precisely where these mobile bugs live (CSS-class
 *   visibility, presence/absence of buttons) — effects don't run, but none of
 *   the asserted behavior depends on effects.
 *
 * All store / api / platform deps are mocked in-file (self-contained, no live
 * backend, no network, deterministic). `mock`-prefixed locals let the hoisted
 * vi.mock factories reference them.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

// ── platform: force the Capacitor mobile branch everywhere ──────────────────
vi.mock("../lib/platform", () => ({
	isMobile: () => true,
	isDesktopApp: () => false,
	isBrowser: () => false,
	detectPlatform: () => "mobile",
}));

// ── store: a tiny selector-driven fake. Components call useStore((s) => s.x);
// we hand each selector a fresh snapshot. setState is a no-op so a render that
// touches it (none should at SSR time) doesn't explode. Only `mock`-prefixed
// module vars may be referenced from the hoisted factory — `mockAgents` is the
// one mutable input (so a test can register agents for ConflictPart's nameOf).
const mockAgents: Array<{ id: string; name: string; color?: string }> = [];
vi.mock("../store", () => {
	const snapshot = () => ({
		agents: mockAgents,
		openPreviewFile: () => {},
		openAgentDetail: () => {},
		replyingTo: null,
		setReplyingTo: () => {},
		composerDraft: null,
		setComposerDraft: () => {},
		preview: { data: {} },
	});
	const useStore = (sel?: (s: ReturnType<typeof snapshot>) => unknown) =>
		sel ? sel(snapshot()) : snapshot();
	(useStore as unknown as { setState: unknown }).setState = () => {};
	(useStore as unknown as { getState: unknown }).getState = snapshot;
	return { useStore };
});

// ── api: download is a no-op stub; never touches network ────────────────────
vi.mock("../lib/api", () => ({
	api: {
		downloadWorkspaceFile: () => {},
		setConvDraftAttachments: () => Promise.resolve(),
		setConvDraft: () => Promise.resolve(),
	},
}));

// runtime-config.assetUrl — pure passthrough so links resolve deterministically.
vi.mock("../lib/runtime-config", () => ({ assetUrl: (s: string) => s }));

import { ConflictPart } from "./parts/ConflictPart";
import { FilesPanelPart } from "./parts/FilesPanelPart";
import { TextPart } from "./parts/TextPart";

afterEach(() => {
	vi.clearAllMocks();
	mockAgents.length = 0;
});

// A real workspace-file src — the shape parseWorkspaceFileSrc() accepts, which
// is what routes the row to the in-app download (api.downloadWorkspaceFile).
const WS_FILE_SRC =
	"/api/workspaces/01J0WSABCDEF/files/download?path=report.pdf";

// Extract the download (下载) button's element so we can assert on ITS classes,
// not the whole card's (the row wrapper also carries `group`, which would make a
// naive `.toContain("group")` pass for the wrong reason).
function downloadButtonClass(html: string): string {
	// The 下载 button is the <button> whose text content includes 下载.
	const buttons = html.split("<button").slice(1);
	const dl = buttons.find((b) => b.includes("下载"));
	if (!dl) throw new Error("no 下载 button found in markup");
	const m = /class="([^"]*)"/.exec(dl);
	return m ? m[1] : "";
}

describe("mobile viewport — FilesPanelPart download affordance", () => {
	const filesPayload = {
		kind: "files" as const,
		message: "交付完成",
		files: [{ src: WS_FILE_SRC, name: "report.pdf", size_bytes: 2048 }],
		links: [],
	};

	it("(1) download button is ALWAYS VISIBLE on mobile — no opacity-0/group-hover gate", () => {
		// Touch has no hover. The desktop tray hides the button behind
		// `opacity-0 group-hover:opacity-100`; on mobile that would make download
		// permanently invisible (the real bug that was fixed). Lock it: on mobile
		// the button must NOT carry the hover-reveal classes.
		const html = renderToStaticMarkup(
			<FilesPanelPart payload={filesPayload} />,
		);
		expect(html).toContain("下载");
		const cls = downloadButtonClass(html);
		expect(cls).not.toContain("opacity-0");
		expect(cls).not.toContain("group-hover:opacity-100");
	});

	it("renders the file row + size, and download wiring is present", () => {
		const html = renderToStaticMarkup(
			<FilesPanelPart payload={filesPayload} />,
		);
		expect(html).toContain("report.pdf");
		expect(html).toContain("交付物"); // deliverable header
		expect(html).toContain("2.0 KB"); // formatBytes(2048)
	});

	it("does not crash on a deliverable with zero files / null links (empty panel)", () => {
		const html = renderToStaticMarkup(
			<FilesPanelPart
				payload={{ kind: "files", message: "", files: [], links: null }}
			/>,
		);
		// header still renders; no row list, no 下载 button when empty.
		expect(html).toContain("交付物");
		expect(html).not.toContain("下载");
	});
});

describe("mobile viewport — ConflictPart is read-only (no manual side-picking)", () => {
	const baseConflict = (over: Record<string, unknown> = {}) => ({
		kind: "conflict" as const,
		conflict_id: "01J0CONFLICT",
		conv_id: "01J0CONV",
		branch: "feature/x",
		agent_id: "agent-1",
		into: "main",
		status: "resolving" as const,
		files: [{ path: "src/app.ts", ctype: "content" as const }],
		...over,
	});

	it("(2) resolving conflict shows '自动解决中' and NO manual resolve/abandon buttons", () => {
		const html = renderToStaticMarkup(
			<ConflictPart payload={baseConflict({ status: "resolving" })} />,
		);
		expect(html).toContain("自动解决中");
		// The whole manual-resolution UX is retired: there must be NO control that
		// lets a phone user pick a side or abandon. Assert on the action verbs that
		// a resolve pane would carry.
		expect(html).not.toContain("采用我方");
		expect(html).not.toContain("采用对方");
		expect(html).not.toContain("放弃合并");
		// And concretely: the card renders zero <button> elements at all.
		expect(html).not.toContain("<button");
	});

	it("an 'open' conflict is equally read-only (still auto-resolving, no buttons)", () => {
		const html = renderToStaticMarkup(
			<ConflictPart payload={baseConflict({ status: "open" })} />,
		);
		expect(html).toContain("自动解决中");
		expect(html).not.toContain("<button");
	});

	it("a resolved conflict shows the agent auto-fix outcome, still no buttons", () => {
		mockAgents.push({ id: "orc", name: "协调者" });
		const html = renderToStaticMarkup(
			<ConflictPart
				payload={baseConflict({
					status: "resolved",
					resolved_by: "orc",
					resolved_sha: "abcdef1234567890",
				})}
			/>,
		);
		expect(html).toContain("协调者 自动解决");
		expect(html).toContain("main@abcdef123");
		expect(html).not.toContain("<button");
	});
});

describe("mobile viewport — TextPart wide content cannot blow the layout", () => {
	const text = (s: string): import("../lib/types").TextPayload => ({
		kind: "text",
		body: [{ t: "p", c: s }],
	});

	it("(3) a wide code block lives inside an overflow container (no horizontal blow-out)", () => {
		// A code line far wider than any phone viewport. The <pre> must scroll, not
		// stretch the message column. TextPart renders fenced code via CodeBlock,
		// whose <pre> carries `overflow-auto` and whose wrapper carries
		// `overflow-hidden` — the two together clip + scroll instead of overflowing.
		const wide = "x".repeat(400);
		const html = renderToStaticMarkup(
			<TextPart payload={text("```js\nconst a = '" + wide + "';\n```")} />,
		);
		expect(html).toContain("<pre");
		expect(html).toContain("overflow-auto"); // the <pre> scrolls
		expect(html).toContain("overflow-hidden"); // the code card clips
	});

	it("a wide GFM table is wrapped in a horizontal-scroll container", () => {
		// Markdown table component wraps <table> in `overflow-x-auto`. Without it a
		// 6-column table on a 390px phone pushes the chat width and breaks the
		// single-column layout.
		const md = [
			"| a | b | c | d | e | f |",
			"| - | - | - | - | - | - |",
			"| 1111111111 | 2222222222 | 3333333333 | 4444444444 | 5555555555 | 6666666666 |",
		].join("\n");
		const html = renderToStaticMarkup(<TextPart payload={text(md)} />);
		expect(html).toContain("<table");
		expect(html).toContain("overflow-x-auto");
	});

	it("a single very long unbroken token does not require an explicit width (no fixed px width leaks)", () => {
		const html = renderToStaticMarkup(
			<TextPart
				payload={text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")}
			/>,
		);
		// Sanity: a plain paragraph must never introduce a hard pixel width that
		// would force horizontal scroll on a narrow viewport.
		expect(html).not.toMatch(/width:\s*\d{3,}px/);
	});
});

// ── (4) Composer / send affordance ──────────────────────────────────────────
// Imported lazily AFTER the mocks above are registered. Composer pulls
// useStore + api; both are mocked. renderToStaticMarkup runs effect-free so the
// textarea auto-resize effect (reads scrollHeight) never fires — safe.
import { Composer } from "./Composer";

describe("mobile viewport — composer send affordance present", () => {
	const renderComposer = () =>
		renderToStaticMarkup(
			<Composer onSend={() => {}} members={["agent-1"]} convId="01J0CONV" />,
		);

	it("(4) the send button is rendered on mobile (title '发送 (Enter)', ArrowUp icon)", () => {
		const html = renderComposer();
		// The send affordance — its title is stable; lucide ArrowUp renders an SVG.
		expect(html).toContain("发送 (Enter)");
		expect(html).toContain("<svg"); // ArrowUp / Paperclip icons
	});

	it("the attach (paperclip) affordance is present and a file input exists", () => {
		const html = renderComposer();
		expect(html).toContain("添加附件");
		expect(html).toContain('type="file"');
	});

	it("with no text the send button is disabled (gate matches submit())", () => {
		// Empty composer → send must be disabled so a phone tap can't fire an empty
		// turn. The button's disabled attr is part of the server markup.
		const html = renderComposer();
		const sendBtnFrag =
			html.split("<button").find((b) => b.includes("发送 (Enter)")) ?? "";
		expect(sendBtnFrag).toContain("disabled");
	});
});
