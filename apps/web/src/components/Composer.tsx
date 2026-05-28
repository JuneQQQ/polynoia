/** Composer — 消息输入框 + @-mention picker + 工具栏
 *
 * @-picker 行为(模仿 Slack / Linear):
 *   - 输入 "@" 时弹出 picker
 *   - 实时 fuzzy filter:"@cl" → ClaudeCode / Orchestrator(@orc)等命中
 *   - ↑↓ 选,Enter / Tab 插入,Esc 关闭
 *   - 插入后光标位置正确;同一行可多次 @
 *   - picker 列表:本 conv 的 members + 所有 enabled adapter agents(全局可召唤)
 */
import { AtSign, Paperclip, Reply, Send, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Agent } from "../lib/types";
import { useStore } from "../store";

type Props = {
  onSend: (text: string, inReplyTo?: string) => void;
  members: string[];
  /** Active conv id — used to scope the global replyingTo state to THIS
   * conv (so switching convs doesn't show a stale reply chip). */
  convId: string;
  /** Pasted image — Composer converts blob to data URL and forwards.
   * Optional: if not provided, paste falls through to default behavior. */
  onAttachImage?: (payload: {
    kind: "image";
    src: string;
    name?: string;
    media_type?: string;
  }) => void;
  /** Pasted / picked non-image file. Same data-URL contract as image. */
  onAttachFile?: (payload: {
    kind: "file";
    src: string;
    name: string;
    media_type?: string;
    size_bytes?: number;
  }) => void;
};

/**
 * Find an "@<query>" token where the caret sits inside it.
 *
 * @ must be at the start of the input, after whitespace, or after a newline
 * — not in the middle of an email-like string. Returns the start position of
 * "@" and the current query (chars after @), or null if no active @ context.
 */
function detectMentionContext(value: string, caret: number): {
  atIndex: number;
  query: string;
} | null {
  // Scan backwards from caret to find an @ that begins a mention.
  for (let i = caret - 1; i >= 0; i--) {
    const ch = value[i];
    // Stop at whitespace / punctuation that ends a mention candidate
    if (ch === " " || ch === "\n" || ch === "\t") return null;
    if (ch === "@") {
      // Ensure @ is at start or preceded by whitespace (avoid email patterns)
      const prev = i === 0 ? " " : value[i - 1];
      if (prev !== " " && prev !== "\n" && prev !== "\t" && i !== 0) return null;
      return { atIndex: i, query: value.slice(i + 1, caret) };
    }
  }
  return null;
}

/** Score-based fuzzy match. Higher = better. Returns -1 if no match. */
function fuzzyScore(needle: string, hay: string): number {
  if (!needle) return 1;
  const n = needle.toLowerCase();
  const h = hay.toLowerCase();
  if (h === n) return 100;
  if (h.startsWith(n)) return 80;
  if (h.includes(n)) return 60;
  // letter-by-letter subsequence(用于 "cc" 匹配 "claudeCode")
  let i = 0;
  let j = 0;
  let lastJ = -1;
  while (i < n.length && j < h.length) {
    if (n[i] === h[j]) {
      if (lastJ >= 0 && j - lastJ > 4) return -1; // 字符跨度太大不算
      lastJ = j;
      i++;
    }
    j++;
  }
  return i === n.length ? 40 : -1;
}

export function Composer({ onSend, members, convId, onAttachImage, onAttachFile }: Props) {
  const [text, setText] = useState("");
  const agents = useStore((s) => s.agents);
  const replyingToRaw = useStore((s) => s.replyingTo);
  const setReplyingTo = useStore((s) => s.setReplyingTo);
  // Only show reply chip when the global state targets THIS conv.
  const replyingTo = replyingToRaw && replyingToRaw.convId === convId ? replyingToRaw : null;
  const isGroup = members.length > 2;
  const otherId = members.find((m) => m !== "you");
  const otherAgent = otherId ? agents.find((a) => a.id === otherId) : null;
  const taRef = useRef<HTMLTextAreaElement>(null);

  // @-picker state
  const [mention, setMention] = useState<{ atIndex: number; query: string } | null>(null);
  const [pickerIdx, setPickerIdx] = useState(0);

  // Candidates pool: this conv's members + every enabled adapter agent
  // (so user can summon someone NOT in the conv yet — server side already
  // accepts mention chain to any agent).
  const candidates: Agent[] = useMemo(() => {
    const out = new Map<string, Agent>();
    for (const id of members) {
      if (id === "you") continue;
      const a = agents.find((x) => x.id === id);
      if (a) out.set(a.id, a);
    }
    for (const a of agents) {
      if (a.id === "you" || a.id === "system") continue;
      if (a.enabled === false) continue;
      out.set(a.id, a);
    }
    return Array.from(out.values());
  }, [members, agents]);

  // Filtered + scored candidates given current query
  const filtered = useMemo(() => {
    if (!mention) return [] as Agent[];
    const q = mention.query;
    const scored = candidates
      .map((a) => {
        const score = Math.max(
          fuzzyScore(q, a.id),
          fuzzyScore(q, a.name),
          fuzzyScore(q, a.handle?.replace(/^@/, "") ?? ""),
          fuzzyScore(q, a.role ?? ""),
        );
        return { a, score };
      })
      .filter((x) => x.score > 0)
      .sort((x, y) => y.score - x.score);
    return scored.slice(0, 8).map((x) => x.a);
  }, [mention, candidates]);

  // Keep pickerIdx valid as filtered changes
  useEffect(() => {
    if (filtered.length === 0) setPickerIdx(0);
    else if (pickerIdx >= filtered.length) setPickerIdx(0);
  }, [filtered, pickerIdx]);

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    onSend(t, replyingTo?.msgId);
    setText("");
    setMention(null);
    if (replyingTo) setReplyingTo(null);
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setText(v);
    const caret = e.target.selectionStart ?? v.length;
    setMention(detectMentionContext(v, caret));
  };

  // Generic file-to-data-URL pipeline shared by paste + picker. Returns
  // a Promise so callers can chain. Caps at 5MB to keep sqlite payload
  // column reasonable (P0 inline; P1+ real upload endpoint).
  const fileToDataUrl = (file: File): Promise<string | null> =>
    new Promise((resolve) => {
      if (file.size > 5 * 1024 * 1024) {
        console.warn(`polynoia: ${file.name} > 5MB skipped`);
        resolve(null);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const v = String(reader.result || "");
        resolve(v.startsWith("data:") ? v : null);
      };
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(file);
    });

  // Dispatch a file to the right handler based on MIME type.
  const dispatchAttachment = async (file: File) => {
    const src = await fileToDataUrl(file);
    if (!src) return;
    if (file.type.startsWith("image/") && onAttachImage) {
      onAttachImage({
        kind: "image",
        src,
        name: file.name || "pasted-image",
        media_type: file.type,
      });
    } else if (onAttachFile) {
      onAttachFile({
        kind: "file",
        src,
        name: file.name || "attachment",
        media_type: file.type || undefined,
        size_bytes: file.size,
      });
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const fileItems = Array.from(items).filter((it) => it.kind === "file");
    if (fileItems.length === 0) return;
    e.preventDefault();
    for (const item of fileItems) {
      const file = item.getAsFile();
      if (file) dispatchAttachment(file);
    }
  };

  // Hidden <input type="file"> driven by the paperclip icon click.
  const fileInputRef = useRef<HTMLInputElement>(null);
  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    for (const f of files) dispatchAttachment(f);
    // Reset so picking the SAME file twice still triggers onChange.
    e.target.value = "";
  };

  const handleSelect = () => {
    // Caret moved (click / arrow keys) — re-detect mention context
    const ta = taRef.current;
    if (!ta) return;
    const caret = ta.selectionStart ?? text.length;
    setMention(detectMentionContext(text, caret));
  };

  const insertMention = (agent: Agent) => {
    if (!mention) return;
    const before = text.slice(0, mention.atIndex);
    const afterQueryStart = mention.atIndex + 1 + mention.query.length;
    const after = text.slice(afterQueryStart);
    // Insert the agent's display name (`@林知夏 ` not `@01KSQ...`). The
    // server's mention parser resolves names back to ids via conv members.
    // For template adapter agents the name and id are basically the same
    // (e.g. "Orchestrator" / "Claude Code"); for custom contacts the name
    // is human-readable while the id is a ULID — only the name is usable
    // in chat copy.
    const inserted = `@${agent.name} `;
    const next = before + inserted + after;
    setText(next);
    setMention(null);
    // Place caret right after the inserted token + trailing space
    const newCaret = before.length + inserted.length;
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) {
        ta.focus();
        ta.setSelectionRange(newCaret, newCaret);
      }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Picker keyboard control
    if (mention && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPickerIdx((i) => (i + 1) % filtered.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPickerIdx((i) => (i - 1 + filtered.length) % filtered.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const target = filtered[pickerIdx];
        if (target) insertMention(target);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    // Normal submit
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const placeholder = isGroup
    ? "发消息给群聊 · 输入 @ 召唤 Agent"
    : `发消息给 ${otherAgent?.name ?? "Agent"} · 输入 @ 召唤其他 Agent`;

  return (
    <div className="border-t border-[var(--color-line-hair)] bg-[var(--color-bg)]">
      <div className="px-6 py-4 relative">
        {/* @-mention picker */}
        {mention && filtered.length > 0 && (
          <div className="absolute bottom-full left-5 right-5 mb-1 z-30 bg-[var(--color-surface)] border border-[var(--color-line)] rounded-lg shadow-lg overflow-hidden max-h-[280px] overflow-y-auto">
            <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] border-b border-[var(--color-line)]/50 bg-[var(--color-surface-2)]">
              召唤 Agent — ↑↓ 选择 · Enter / Tab 插入 · Esc 取消
            </div>
            <ul>
              {filtered.map((a, i) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onMouseEnter={() => setPickerIdx(i)}
                    onMouseDown={(e) => {
                      // mouseDown before blur so caret stays
                      e.preventDefault();
                      insertMention(a);
                    }}
                    className={`flex items-center gap-2.5 w-full px-3 py-1.5 text-left transition ${
                      i === pickerIdx ? "bg-[var(--color-accent-soft)]" : "hover:bg-[var(--color-surface-2)]"
                    }`}
                  >
                    <div
                      className="w-7 h-7 rounded-md grid place-items-center text-white text-[10px] font-medium flex-shrink-0"
                      style={{ background: a.color }}
                    >
                      {a.initials}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[12.5px] font-medium truncate">
                        {a.name}
                        <span className="ml-1.5 text-[10.5px] text-[var(--color-fg-3)] font-normal">
                          @{a.id}
                        </span>
                      </div>
                      {(a.tagline || a.role) && (
                        <div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
                          {a.tagline ?? a.role}
                        </div>
                      )}
                    </div>
                    {members.includes(a.id) && (
                      <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-[var(--color-green-soft)] text-[var(--color-green)] flex-shrink-0">
                        本群
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Reply chip — appears above the textarea when user clicked Reply
            on a message. Shows sender + snippet + close. Consumed by ChatPane
            via the global replyingTo store state (cleared after send). */}
        {replyingTo && (
          <div className="mb-2 flex items-center gap-2 px-2.5 py-1.5 rounded-sm bg-[var(--color-accent-soft)] border-l-2 border-[var(--color-accent)] text-[11.5px] anim-fade-up">
            <Reply size={11} className="text-[var(--color-accent)] flex-shrink-0" />
            <span className="text-[var(--color-accent)] font-medium flex-shrink-0">
              回复
            </span>
            <span className="font-medium text-[var(--color-fg-2)] flex-shrink-0">
              {replyingTo.senderLabel}
            </span>
            <span className="text-[var(--color-fg-3)] truncate min-w-0 flex-1">
              {replyingTo.snippet}
            </span>
            <button
              type="button"
              onClick={() => setReplyingTo(null)}
              className="flex-shrink-0 p-0.5 rounded-sm hover:bg-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
              title="取消回复"
            >
              <X size={11} />
            </button>
          </div>
        )}

        {/* Editorial-style input: no chunky border, just a hair-line bottom
            that turns orange on focus. Surface stays bg color so the input
            visually belongs to the page, not to a "control container". */}
        <div className="relative">
          <textarea
            ref={taRef}
            value={text}
            onChange={handleChange}
            onKeyUp={handleSelect}
            onClick={handleSelect}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={placeholder}
            rows={2}
            className="w-full resize-none bg-transparent outline-none text-[14px] leading-relaxed text-[var(--color-fg)] placeholder:text-[var(--color-fg-4)] min-h-[52px] max-h-[200px] pb-2 pt-1 pr-2 border-b border-[var(--color-line-strong)] focus:border-[var(--color-accent)] transition-colors"
          />
        </div>
        <div className="flex items-center gap-2 mt-3">
          {isGroup ? (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] px-2 py-1 text-[var(--color-fg-3)]">
              <AtSign size={11} className="text-[var(--color-purple)]" />
              <span className="font-mono">orchestrator</span>
              <span className="text-[var(--color-fg-4)]">· 自动分派</span>
            </span>
          ) : otherAgent ? (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] px-2 py-1 text-[var(--color-fg-2)]">
              <span
                className="w-3.5 h-3.5 rounded text-[9px] text-white grid place-items-center"
                style={{ background: otherAgent.color }}
              >
                {otherAgent.initials}
              </span>
              {otherAgent.name}
              <span className="text-[var(--color-fg-4)]">· 1v1</span>
            </span>
          ) : null}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={onPickFiles}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="ml-auto p-1.5 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
            title="添加附件(也支持粘贴)"
          >
            <Paperclip size={14} />
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            className="btn-primary text-[12.5px] py-1.5 px-3.5"
          >
            发送 <Send size={11} />
          </button>
        </div>
      </div>
    </div>
  );
}
