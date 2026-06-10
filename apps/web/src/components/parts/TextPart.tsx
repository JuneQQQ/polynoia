/** TextPart — renders message body as Markdown(GFM)+ inline @mention chips
 *  + syntax-highlighted code blocks.
 *
 *  Two body shapes supported (Polynoia 协议):
 *    1. `c: string` — pure markdown, rendered straight through
 *    2. `c: Array<{type:"text"|"mention", ...}>` — structured inline segments
 *       (mock orchestrator uses this for @mention; future agents may too)
 *
 *  For (2) we flatten to markdown by replacing mention segments with a sentinel
 *  token, then post-process the rendered tree to swap the sentinel for a chip.
 *  Implementation:we pre-render structured content directly (no markdown
 *  pass);for string content we go through react-markdown.
 */
import { Check, Copy } from "lucide-react";
import { Children, memo, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { InlineSegment, TextPayload } from "../../lib/types";
import { useStore } from "../../store";

// Highlight.js styles — picked a warm-neutral light theme matching Polynoia palette
import "highlight.js/styles/github.css";

function Mention({ agentId }: { agentId: string }) {
	const agents = useStore((s) => s.agents);
	const openAgentDetail = useStore((s) => s.openAgentDetail);
	const agent = agents.find((a) => a.id === agentId);
	// Unrecognized → plain muted text (no emphasis).
	if (!agent) {
		return (
			<span className="text-[var(--color-fg-3)] font-medium">@{agentId}</span>
		);
	}
	// Recognized member → clean inline mention (Slack/Linear style): member-colored
	// text on a faint same-color tint, no border, no dot. Clicking opens the agent.
	const c = agent.color || "var(--color-accent)";
	return (
		<button
			type="button"
			onClick={() => openAgentDetail(agent.id)}
			title={`查看 @${agent.name}`}
			className="inline rounded px-[3px] py-px font-medium align-baseline whitespace-nowrap transition-colors cursor-pointer hover:brightness-95"
			style={{
				color: c,
				background: `color-mix(in srgb, ${c} 12%, transparent)`,
			}}
		>
			<span style={{ opacity: 0.6 }}>@</span>
			{agent.name}
		</button>
	);
}

// Build a regex over known member names so plain-text "@林知夏" in a markdown
// string also renders as a recognized-member chip (not only structured segments).
function escapeRe(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function useMentionSplitter() {
	const agents = useStore((s) => s.agents);
	return useMemo(() => {
		const named = agents.filter((a) => a.name);
		// Resolve @<name> AND @<agent_id> (ULID). Agents — especially the
		// orchestrator's wrap-up summary — often emit the raw contact id, which
		// would otherwise render as an ugly "@<26-char ULID>" instead of a clean
		// member chip.
		const byKey = new Map<string, (typeof named)[number]>();
		for (const a of named) {
			byKey.set(a.name, a);
			if (a.id) byKey.set(a.id, a);
		}
		// longest-first so a token that prefixes another still wins the greedy match
		const tokens = [...byKey.keys()].sort((a, b) => b.length - a.length);
		const re = tokens.length
			? new RegExp(`@(${tokens.map(escapeRe).join("|")})`, "g")
			: null;
		return { re, byName: byKey };
	}, [agents]);
}

/** Split string children on recognized @member names → emphasized Mention chips. */
function MentionAware({ children }: { children: React.ReactNode }) {
	const { re, byName } = useMentionSplitter();
	if (!re) return <>{children}</>;
	return (
		<>
			{Children.map(children, (child) => {
				if (typeof child !== "string") return child;
				re.lastIndex = 0;
				let m: RegExpExecArray | null = re.exec(child);
				if (m === null) return child;
				const parts: React.ReactNode[] = [];
				let last = 0;
				let k = 0;
				while (m !== null) {
					if (m.index > last) parts.push(child.slice(last, m.index));
					const agent = byName.get(m[1]);
					parts.push(
						<Mention key={`m${k++}`} agentId={agent ? agent.id : m[1]} />,
					);
					last = m.index + m[0].length;
					m = re.exec(child);
				}
				if (last < child.length) parts.push(child.slice(last));
				return <>{parts}</>;
			})}
		</>
	);
}

function CodeBlock({
	className,
	children,
}: {
	className?: string;
	children?: React.ReactNode;
}) {
	const [copied, setCopied] = useState(false);
	// react-markdown v9 removed the `inline` prop. We detect inline vs block
	// ourselves:
	//   · `language-xxx` className → fenced code block (always block)
	//   · multi-line content → must be block (inline can't contain newlines)
	//   · otherwise → inline `…` backticks → render as <code> chip
	// Without this branch, single-char inline backticks like `` `(` `` get
	// rendered as a giant bordered block with "TEXT" + "复制" labels, which
	// showed up in the user's leetcode-style prompt as garbled output.
	const raw = String(children ?? "");
	const hasLang = (className ?? "").startsWith("language-");
	const isMultiline = raw.includes("\n");
	const isInline = !hasLang && !isMultiline;
	if (isInline) {
		return (
			<code className="mx-[1px] rounded-md border border-[var(--color-line)] bg-[var(--color-code-bg)] px-1.5 py-[2px] align-[0.04em] font-mono text-[0.84em] leading-none text-[var(--color-code-fg)] box-decoration-clone break-words">
				{children}
			</code>
		);
	}
	const lang = /language-(\w+)/.exec(className ?? "")?.[1];
	const text = raw.replace(/\n$/, "");
	return (
		<div className="group relative my-2.5 overflow-hidden rounded-lg border border-[var(--color-line-2)] bg-[var(--color-code-block-bg)] shadow-[var(--shadow-sm)]">
			<div className="flex h-8 items-center gap-2 border-b border-[var(--color-line)] bg-[var(--color-code-header-bg)] px-3 text-[10.5px] text-[var(--color-fg-3)] mono">
				<span className="flex items-center gap-1.5" aria-hidden>
					<span className="h-2 w-2 rounded-full bg-[var(--color-red)]/70" />
					<span className="h-2 w-2 rounded-full bg-[var(--color-amber)]/70" />
					<span className="h-2 w-2 rounded-full bg-[var(--color-green)]/70" />
				</span>
				<span className="rounded bg-[var(--color-surface)]/70 px-1.5 py-[1px] uppercase tracking-[0.14em] text-[var(--color-fg-3)]">
					{lang || "text"}
				</span>
				<button
					type="button"
					onClick={() => {
						navigator.clipboard.writeText(text);
						setCopied(true);
						setTimeout(() => setCopied(false), 1500);
					}}
					className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-[var(--color-fg-3)] transition hover:bg-[var(--color-line)] hover:text-[var(--color-fg)]"
				>
					{copied ? <Check size={10} /> : <Copy size={10} />}
					{copied ? "已复制" : "复制"}
				</button>
			</div>
			<pre
				className="m-0 max-h-[460px] overflow-auto px-3.5 py-3 font-mono text-[12.5px] leading-[1.65] text-[var(--color-fg-2)]"
				style={{ tabSize: 2 }}
			>
				<code className={`${className ?? ""} block min-w-max whitespace-pre bg-transparent`}>
					{children}
				</code>
			</pre>
		</div>
	);
}

/** Render a structured inline c: Array<InlineSegment>(non-markdown path). */
function StructuredInline({ content }: { content: InlineSegment[] }) {
	return (
		<>
			{content.map((seg, i) => {
				if (seg.type === "text")
					return <InlineMarkdown key={i} text={seg.text} />;
				return <Mention key={i} agentId={seg.m} />;
			})}
		</>
	);
}

// Exported so the code-editor Markdown preview (CodeEditor) renders docs with
// the exact same styling as chat messages.
export const MARKDOWN_COMPONENTS = {
	code: CodeBlock as any,
	// Tables — make them look right with Polynoia palette
	table: ({ children }: any) => (
		<div className="overflow-x-auto my-2">
			<table className="w-full text-[12px] border-collapse">{children}</table>
		</div>
	),
	thead: ({ children }: any) => (
		<thead className="bg-[var(--color-surface-2)]">{children}</thead>
	),
	th: ({ children }: any) => (
		<th className="text-left px-2 py-1 border border-[var(--color-line)] font-semibold text-[10.5px] uppercase tracking-wider text-[var(--color-fg-3)]">
			{children}
		</th>
	),
	td: ({ children }: any) => (
		<td className="px-2 py-1 border border-[var(--color-line)]">{children}</td>
	),
	a: ({ href, children }: any) => (
		<a
			href={href}
			target="_blank"
			rel="noreferrer noopener"
			className="text-[var(--color-accent)] underline underline-offset-2 hover:opacity-80"
		>
			{children}
		</a>
	),
	// Headings — markdown headers in chat look weird as h1/h2; tone them down
	h1: ({ children }: any) => (
		<div className="text-[15px] font-bold mt-3 mb-1.5">{children}</div>
	),
	h2: ({ children }: any) => (
		<div className="text-[14px] font-semibold mt-2.5 mb-1">{children}</div>
	),
	h3: ({ children }: any) => (
		<div className="text-[13px] font-semibold mt-2 mb-1 text-[var(--color-fg-2)]">
			{children}
		</div>
	),
	ul: ({ children }: any) => (
		<ul className="list-disc pl-5 my-1.5 space-y-0.5">{children}</ul>
	),
	ol: ({ children }: any) => (
		<ol className="list-decimal pl-5 my-1.5 space-y-0.5">{children}</ol>
	),
	li: ({ children }: any) => (
		<li className="leading-relaxed">
			<MentionAware>{children}</MentionAware>
		</li>
	),
	blockquote: ({ children }: any) => (
		<blockquote className="border-l-2 border-[var(--color-accent)] pl-3 my-1.5 text-[var(--color-fg-3)]">
			{children}
		</blockquote>
	),
	hr: () => <hr className="my-3 border-[var(--color-line)]" />,
	// react-markdown 9 + custom code components can put block-level code blocks
	// (<div><pre>) inside <p>, triggering DOM nesting warnings. Render paragraph
	// as <div> instead so any child is legal regardless of code-block detection.
	p: ({ children }: any) => (
		<div className="my-1 leading-relaxed">
			<MentionAware>{children}</MentionAware>
		</div>
	),
	strong: ({ children }: any) => (
		<strong className="font-semibold">{children}</strong>
	),
	em: ({ children }: any) => <em className="italic">{children}</em>,
};

const INLINE_MARKDOWN_COMPONENTS = {
	...MARKDOWN_COMPONENTS,
	p: ({ children }: any) => <MentionAware>{children}</MentionAware>,
	// Structured inline segments live inside one paragraph; fenced/list/table
	// markdown belongs to string blocks. Keep accidental block constructs inline.
	ul: ({ children }: any) => <span>{children}</span>,
	ol: ({ children }: any) => <span>{children}</span>,
	li: ({ children }: any) => <span>{children}</span>,
};

function InlineMarkdown({ text }: { text: string }) {
	if (!text) return null;
	return (
		<ReactMarkdown
			remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
			components={INLINE_MARKDOWN_COMPONENTS as any}
		>
			{fixCjkMarkdown(stripRawToolProtocol(text))}
		</ReactMarkdown>
	);
}

/**
 * Find a "safe split point" in streaming markdown — where the prefix above
 * can be considered finalized (won't re-parse differently when more text
 * arrives below). We use the LATEST `\n\n` (paragraph boundary) that is
 * NOT inside an open code fence.
 *
 * Why this matters:
 *   Markdown is context-sensitive. `---` alone on a line is an <hr> only
 *   if it's followed by content that doesn't merge it into a setext heading
 *   etc. As deltas arrive, parse can flip-flop. Rendering each delta
 *   re-parses the whole text and the parse tree mutates — visible as the
 *   "`--` → `<hr>` → `--`" wobble the user reported.
 *
 *   By splitting on the latest paragraph boundary, the prefix becomes a
 *   closed markdown unit (the next paragraph below is independent), so
 *   its render is stable. Only the tail (current in-flight paragraph)
 *   stays raw / pre-wrap.
 */
/**
 * Pre-process for CommonMark CJK gotcha:
 *   `**第三人（最后面）**看了看` → `**` after `）` then `看` doesn't satisfy
 *   the right-flanking delimiter run rules,so bold never closes,asterisks
 *   leak as literals.
 *
 * Inject a U+200B zero-width space between CJK boundary char and the
 * closing `**`/`__` so the delimiter is properly recognized.
 *
 * Cheap pre-process beats pulling in `remark-cjk-friendly` (which conflicts
 * with this repo's workspace lockfile).
 */
// CLOSING-side normalization ONLY: a CJK ideograph or fullwidth char (including a
// fullwidth `）`) sitting immediately before a closing `**`/`__`. Insert a U+200B
// so the delimiter satisfies CommonMark's right-flanking rule and the bold closes
// — otherwise `**说明（重要）**结论` leaks literal asterisks.
//
// We deliberately do NOT touch the OPENING side (`**` followed by CJK): the old
// opening-side ZWSP made `**顾屿 ✓**` render literally (the user's bug), so it stays
// removed. This regex only matches a char *before* `**`, so it can never insert a
// ZWSP *after* an opening `**` → structurally cannot reintroduce that bug. Both
// directions are pinned in TextPart.cjkMarkdown.test.tsx against the real
// react-markdown + remark-gfm render path.
const CJK_CLOSE_RE = /([一-鿿　-〿＀-￯])(\*\*|__)/g;
export function fixCjkMarkdown(s: string): string {
	return s.replace(CJK_CLOSE_RE, "$1​$2");
}

const RAW_TOOL_MARKER_RE = /<(?:tool_call|tool_result|tool_response)>/g;
const RAW_TOOL_CLOSE_RE = /<\/(?:tool_call|tool_result|tool_response)>/g;
const HIDDEN_TOOL_NOTICE = "> 工具调用协议内容已隐藏。";

function findJsonLikeEnd(text: string, start: number): number | null {
	const opener = text[start];
	const closer = opener === "{" ? "}" : opener === "[" ? "]" : null;
	if (!closer) return null;
	const stack: string[] = [closer];
	let inString = false;
	let escaped = false;
	for (let i = start + 1; i < text.length; i++) {
		const ch = text[i];
		if (inString) {
			if (escaped) {
				escaped = false;
			} else if (ch === "\\") {
				escaped = true;
			} else if (ch === '"') {
				inString = false;
			}
			continue;
		}
		if (ch === '"') {
			inString = true;
			continue;
		}
		if (ch === "{") {
			stack.push("}");
			continue;
		}
		if (ch === "[") {
			stack.push("]");
			continue;
		}
		if (ch === stack[stack.length - 1]) {
			stack.pop();
			if (stack.length === 0) return i + 1;
		}
	}
	return null;
}

export function stripRawToolProtocol(text: string): string {
	if (!text.includes("<tool_")) return text;
	RAW_TOOL_MARKER_RE.lastIndex = 0;
	let out = "";
	let cursor = 0;
	let hidden = 0;
	let match: RegExpExecArray | null = RAW_TOOL_MARKER_RE.exec(text);
	while (match) {
		out += text.slice(cursor, match.index);
		let i = match.index + match[0].length;
		while (i < text.length && /\s/.test(text[i])) i++;
		const end = findJsonLikeEnd(text, i);
		hidden += 1;
		if (end === null) {
			cursor = text.length;
			break;
		}
		cursor = end;
		const close = /^<\/(?:tool_call|tool_result|tool_response)>/.exec(text.slice(cursor));
		if (close) cursor += close[0].length;
		RAW_TOOL_MARKER_RE.lastIndex = cursor;
		match = RAW_TOOL_MARKER_RE.exec(text);
	}
	out += text.slice(cursor);
	out = out.replace(RAW_TOOL_CLOSE_RE, "");
	if (hidden === 0) return text;
	const normalized = out.replace(/\n{3,}/g, "\n\n").trim();
	return normalized
		? `${normalized}\n\n${HIDDEN_TOOL_NOTICE}`
		: HIDDEN_TOOL_NOTICE;
}

function findSafeSplitPoint(text: string): number {
	let pos = text.length;
	while (pos > 0) {
		const idx = text.lastIndexOf("\n\n", pos - 1);
		if (idx === -1) return 0;
		// Count ``` fences before this position. Odd = we're inside an open
		// code block → can't split here.
		const before = text.slice(0, idx);
		const fenceCount = (before.match(/```/g) || []).length;
		if (fenceCount % 2 === 0) {
			return idx + 2; // safe split: right after the \n\n
		}
		pos = idx;
	}
	return 0;
}

/**
 * StringBlock — renders one string-content text block.
 *
 * Two modes:
 *   - WHILE streaming (`isStreaming=true`): split text into a *settled prefix*
 *     (everything up to the latest `\n\n` outside a code fence) and a *tail*
 *     (the still-appending current paragraph). Render prefix as markdown,
 *     tail as raw `<div whitespace-pre-wrap>`. The prefix only ever grows,
 *     never re-parses different shapes — so previously-rendered content
 *     (e.g. an <hr> from `---`) stays put once it lands above a `\n\n`.
 *   - AFTER streaming (`isStreaming=false`): full markdown over entire text.
 */
const StringBlock = memo(function StringBlock({
	text,
	isStreaming,
}: {
	text: string;
	isStreaming?: boolean;
}) {
	const displayText = stripRawToolProtocol(text);
	if (!isStreaming) {
		return (
			<ReactMarkdown
				remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
				rehypePlugins={[
					[rehypeHighlight, { detect: true, ignoreMissing: true }],
				]}
				components={MARKDOWN_COMPONENTS as any}
			>
				{fixCjkMarkdown(displayText)}
			</ReactMarkdown>
		);
	}
	// Streaming: prefix (settled) + tail (raw).
	const split = findSafeSplitPoint(displayText);
	const prefix = split > 0 ? displayText.slice(0, split) : "";
	const tail = displayText.slice(split);
	// The settled prefix is append-only and only grows when `split` advances past
	// a new \n\n boundary. Memoize the (expensive) ReactMarkdown + rehypeHighlight
	// parse on `split`, so streaming deltas only re-render the cheap pre-wrap tail
	// instead of re-parsing the whole prefix every delta (O(L²) → O(L)).
	// biome-ignore lint/correctness/useExhaustiveDependencies: prefix is a pure function of `split` (append-only text); split is the minimal stable key.
	const prefixMd = useMemo(
		() =>
			prefix ? (
				<ReactMarkdown
					remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
					rehypePlugins={[
						[rehypeHighlight, { detect: true, ignoreMissing: true }],
					]}
					components={MARKDOWN_COMPONENTS as any}
				>
					{fixCjkMarkdown(prefix)}
				</ReactMarkdown>
			) : null,
		[split],
	);
	return (
		<>
			{prefixMd}
			{tail && (
				<div className="my-1 leading-relaxed whitespace-pre-wrap">
					<MentionAware>{tail}</MentionAware>
				</div>
			)}
		</>
	);
});

/** Markdown — one-shot (non-streaming) GFM render with the chat component map,
 * CJK-bold fix and raw-tool-protocol stripping. Reused by deliverable panels and
 * anywhere agent-authored markdown is shown outside a streaming chat bubble. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
	return (
		<ReactMarkdown
			remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
			rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
			components={MARKDOWN_COMPONENTS as any}
		>
			{fixCjkMarkdown(stripRawToolProtocol(text))}
		</ReactMarkdown>
	);
});

export const TextPart = memo(function TextPart({
	payload,
	isStreaming,
}: {
	payload: TextPayload;
	isStreaming?: boolean;
}) {
	const inner = (
		<div className="text-[13px] text-[var(--color-fg)]">
			{payload.body.map((block, i) =>
				typeof block.c === "string" ? (
					<StringBlock key={i} text={block.c} isStreaming={isStreaming} />
				) : (
					// Structured inline (mention-aware), no markdown pass
					<p key={i} className="my-1 leading-relaxed">
						<StructuredInline content={block.c} />
					</p>
				),
			)}
		</div>
	);
	// A multi-agent discussion's wrap-up (synthesizer leads with 「讨论结论:」) gets
	// a subtle accent card + badge so it reads as the conclusion, not just another
	// reply. Detection is prefix-only (no new payload kind needed).
	const first = payload.body[0]?.c;
	const isSummary =
		typeof first === "string" && /^\s*(?:\*\*|##\s*)?讨论结论/.test(first);
	if (!isSummary) return inner;
	return (
		<div className="rounded-md border border-[var(--color-accent)]/30 border-l-[3px] border-l-[var(--color-accent)] bg-[var(--color-accent-soft)]/30 pl-3 pr-2.5 py-2">
			<div className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-accent)] mb-1 font-medium">
				讨论结论
			</div>
			{inner}
		</div>
	);
});
