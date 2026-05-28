/** MessageView — renders a single conv message.
 *
 * Subscribes to ONE message by (convId, msgId). React.memo + Zustand's
 * `useStore(selector)` shallow-equality means this only re-renders when the
 * selected message changes — critical during multi-agent streaming where
 * every text-delta would otherwise rebuild every message in the conv.
 */
import { CornerUpLeft, Copy, Pin, PinOff, RefreshCw, Reply } from "lucide-react";
import { memo, useState } from "react";
import { api } from "../lib/api";
import { selectIsMessageStreaming, selectMessageById, useStore } from "../store";
import { MessagePart } from "./parts";

type Props = {
  convId: string;
  msgId: string;
  /** True when this message is a continuation of the previous message
   * from the same sender — hide avatar + name + timestamp for visual
   * grouping (tool-call + text from the same agent turn render as one block). */
  isGrouped?: boolean;
};

function MessageViewInner({ convId, msgId, isGrouped }: Props) {
  // Per-message subscription: ChatPane gives us stable ids, store mutates
  // msgById entries in place; this hook only fires when THIS message changes.
  const msg = useStore((s) => selectMessageById(s, convId, msgId));
  const isStreaming = useStore((s) => selectIsMessageStreaming(s, convId, msgId));
  const agents = useStore((s) => s.agents);

  if (!msg) return null;
  const isYou = msg.sender_id === "you";
  const isSystem = msg.sender_id === "system";
  const agent = isSystem ? undefined : agents.find((a) => a.id === msg.sender_id);

  // If this message is a reply, resolve the target for the "回复 @X" header.
  // We peek at the store snapshot (not a subscription) — the parent message
  // doesn't change after creation, so no re-render dependency needed.
  const replyTarget = msg.in_reply_to
    ? useStore.getState().convs.get(convId)?.msgById.get(msg.in_reply_to)
    : null;
  const replyTargetSender = replyTarget
    ? replyTarget.sender_id === "you"
      ? "我"
      : agents.find((a) => a.id === replyTarget.sender_id)?.name ?? "Agent"
    : null;
  const replyTargetSnippet = (() => {
    if (!replyTarget) return "";
    const p = replyTarget.payload as { kind: string; body?: Array<{ c: string }> };
    if (p.kind === "text" && Array.isArray(p.body)) {
      return p.body.map((b) => b.c).join(" ").slice(0, 80);
    }
    return `[${p.kind} card]`;
  })();

  const scrollToReplyTarget = () => {
    if (!msg.in_reply_to) return;
    const el = document.querySelector(`[data-msg-id="${msg.in_reply_to}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      // Brief flash to draw attention
      el.classList.add("flash-target");
      setTimeout(() => el.classList.remove("flash-target"), 1200);
    }
  };

  return (
    <div
      data-msg-id={msg.id}
      className={`anim-fade-up group/msg flex gap-3 px-6 transition-colors duration-200 ${
        isGrouped ? "pt-0.5 pb-0.5" : "pt-3 pb-1.5"
      } ${
        isYou
          ? "bg-[var(--color-surface-2)]/40 hover:bg-[var(--color-surface-2)]/60"
          : "hover:bg-[var(--color-surface-2)]/25"
      }`}
    >
      {/* Avatar column — keep width to preserve indent when grouped */}
      <div className="w-8 flex-shrink-0">
        {!isGrouped && (
          (isYou || isSystem) ? (
            <div
              className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium shadow-sm transition-transform duration-200 group-hover/msg:scale-[1.04]"
              style={{
                background: isYou ? "#5E5749" : "var(--color-red)",
              }}
            >
              {isYou ? "我" : "!"}
            </div>
          ) : (
            <button
              type="button"
              onClick={() => agent && useStore.getState().openAgentDetail(agent.id)}
              className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium shadow-sm transition-all duration-200 group-hover/msg:scale-[1.04] hover:shadow-md hover:ring-2 hover:ring-[var(--color-accent-soft)]"
              style={{ background: agent?.color ?? "var(--color-fg-3)" }}
              title={`查看 ${agent?.name ?? "Agent"} 详情`}
            >
              {agent?.initials ?? "?"}
            </button>
          )
        )}
      </div>
      <div className="flex-1 min-w-0">
        {!isGrouped && (
          <div className="flex items-baseline gap-2 mb-1">
            {(isYou || isSystem) ? (
              <span className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide">
                {isYou ? "我" : "System"}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => agent && useStore.getState().openAgentDetail(agent.id)}
                className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
                title="查看详情"
              >
                {agent?.name ?? "Agent"}
              </button>
            )}
            {!isYou && !isSystem && agent?.id === "orchestrator" && (
              <span
                className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
                style={{ background: agent.bg, color: agent.color }}
              >
                ORCHESTRATOR
              </span>
            )}
            {!isYou && !isSystem && agent?.custom && (
              <span
                className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
                style={{ background: agent.bg, color: agent.color }}
              >
                CUSTOM
              </span>
            )}
            {!isYou && !isSystem && agent?.id !== "orchestrator" && !agent?.custom && (
              <span
                className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
                style={{
                  background: agent?.bg ?? "var(--color-line)",
                  color: agent?.color ?? "var(--color-fg-3)",
                }}
              >
                BOT
              </span>
            )}
            <span className="text-[10px] font-mono text-[var(--color-fg-4)] tabular-nums opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200">
              {new Date(msg.created_at).toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <MessageActions
              msgId={msg.id}
              convId={convId}
              pinned={msg.pinned ?? false}
              isYou={isYou}
            />
          </div>
        )}
        {/* Reply target header — small clickable chip pointing to original */}
        {msg.in_reply_to && replyTarget && (
          <button
            type="button"
            onClick={scrollToReplyTarget}
            className="mb-1 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm bg-[var(--color-surface-2)] hover:bg-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] transition max-w-full"
            title="跳转到原消息"
          >
            <CornerUpLeft size={9} className="flex-shrink-0" />
            <span className="font-medium text-[var(--color-fg-2)]">
              {replyTargetSender}
            </span>
            <span className="truncate opacity-70">{replyTargetSnippet}</span>
          </button>
        )}
        <MessagePart payload={msg.payload} isStreaming={isStreaming} />
      </div>
    </div>
  );
}

function MessageActions({
  msgId,
  convId,
  pinned,
  isYou,
}: {
  msgId: string;
  convId: string;
  pinned: boolean;
  /** Hides "regenerate" for user messages (only makes sense on agent output). */
  isYou: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [regenBusy, setRegenBusy] = useState(false);

  // Extract pure text from the message payload — only TEXT and TOOL-CALL
  // kinds expose a string we can put in clipboard meaningfully. Cards
  // (diff/web/etc) fall back to JSON.
  const copy = async () => {
    const cs = useStore.getState().convs.get(convId);
    const m = cs?.msgById.get(msgId);
    if (!m) return;
    let text = "";
    const p = m.payload as { kind: string; body?: Array<{ c: string }> };
    if (p.kind === "text" && Array.isArray(p.body)) {
      text = p.body.map((b) => b.c).join("\n");
    } else {
      text = JSON.stringify(m.payload, null, 2);
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // ignore — older browsers may not have clipboard API
    }
  };

  // Regenerate — only makes sense on AGENT output. Finds the immediately
  // preceding USER message in this conv and re-fires it via the conv's
  // WS connection. UI removes the current agent message optimistically.
  const regenerate = async () => {
    if (regenBusy) return;
    const cs = useStore.getState().convs.get(convId);
    if (!cs) return;
    const order = cs.messageOrder;
    const idx = order.indexOf(msgId);
    if (idx <= 0) return;
    // Walk back to find the most recent "you" message
    let text = "";
    for (let i = idx - 1; i >= 0; i--) {
      const prev = cs.msgById.get(order[i]);
      if (prev && prev.sender_id === "you") {
        // Avoid the discriminated-union narrowing dance — cast once.
        const p = prev.payload as { kind: string; body?: Array<{ c: string }> };
        if (p.kind === "text" && Array.isArray(p.body)) {
          text = p.body.map((b) => b.c).join("\n");
        }
        break;
      }
    }
    if (!text.trim()) return;
    setRegenBusy(true);
    // Re-fire via ChatPane's WebSocket — we surface that via a window event
    // so we don't have to thread the ws ref through MessageView.
    window.dispatchEvent(
      new CustomEvent("polynoia:regenerate", {
        detail: { convId, text },
      }),
    );
    setTimeout(() => setRegenBusy(false), 1500);
  };

  // Optimistic update — flip the store entry's pinned flag immediately so
  // the icon switches without a refetch.
  const togglePin = async () => {
    if (busy) return;
    setBusy(true);
    const next = !pinned;
    useStore.setState((s) => {
      const cs = s.convs.get(convId);
      if (!cs) return {};
      const m = cs.msgById.get(msgId);
      if (!m) return {};
      const newMsg = { ...m, pinned: next };
      const newMap = new Map(cs.msgById);
      newMap.set(msgId, newMsg);
      const newConvs = new Map(s.convs);
      newConvs.set(convId, { ...cs, msgById: newMap });
      return { convs: newConvs };
    });
    try {
      if (next) await api.pinMessage(msgId);
      else await api.unpinMessage(msgId);
    } catch {
      // Roll back on failure
      useStore.setState((s) => {
        const cs = s.convs.get(convId);
        if (!cs) return {};
        const m = cs.msgById.get(msgId);
        if (!m) return {};
        const newMsg = { ...m, pinned };
        const newMap = new Map(cs.msgById);
        newMap.set(msgId, newMsg);
        const newConvs = new Map(s.convs);
        newConvs.set(convId, { ...cs, msgById: newMap });
        return { convs: newConvs };
      });
    } finally {
      setBusy(false);
    }
  };
  // Reply — set the global replyingTo state. Composer reads it.
  const reply = () => {
    const cs = useStore.getState().convs.get(convId);
    const m = cs?.msgById.get(msgId);
    if (!m) return;
    const p = m.payload as { kind: string; body?: Array<{ c: string }> };
    let snippet = "";
    if (p.kind === "text" && Array.isArray(p.body)) {
      snippet = p.body.map((b) => b.c).join(" ");
    } else {
      snippet = `[${p.kind} card]`;
    }
    const agentsList = useStore.getState().agents;
    const senderLabel =
      m.sender_id === "you"
        ? "我"
        : m.sender_id === "system"
          ? "System"
          : agentsList.find((a) => a.id === m.sender_id)?.name ?? "Agent";
    useStore.getState().setReplyingTo({
      convId,
      msgId,
      snippet: snippet.slice(0, 120),
      senderLabel,
    });
  };

  return (
    <div className="ml-auto flex items-center gap-0.5">
      <button
        type="button"
        onClick={reply}
        title="回复"
        className="p-0.5 rounded-sm opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)] transition-opacity duration-200"
      >
        <Reply size={11} />
      </button>
      <button
        type="button"
        onClick={copy}
        title={copied ? "已复制" : "复制内容"}
        className={`p-0.5 rounded-sm transition-opacity duration-200 ${
          copied
            ? "opacity-90 text-[var(--color-green)]"
            : "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
        }`}
      >
        <Copy size={11} />
      </button>
      {!isYou && (
        <button
          type="button"
          onClick={regenerate}
          disabled={regenBusy}
          title="重新生成"
          className={`p-0.5 rounded-sm transition-opacity duration-200 ${
            regenBusy
              ? "opacity-70 text-[var(--color-accent)]"
              : "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
          }`}
        >
          <RefreshCw size={11} className={regenBusy ? "animate-spin" : ""} />
        </button>
      )}
      <button
        type="button"
        onClick={togglePin}
        disabled={busy}
        title={pinned ? "取消置顶" : "置顶消息"}
        className={`p-0.5 rounded-sm transition-opacity duration-200 ${
          pinned
            ? "opacity-90 text-[var(--color-accent)]"
            : "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
        }`}
      >
        {pinned ? <PinOff size={11} /> : <Pin size={11} />}
      </button>
    </div>
  );
}

/**
 * Memo'd at the (convId, msgId) boundary. Combined with the per-message
 * Zustand selector above, a text-delta to message X only re-renders X's
 * MessageView, NOT all sibling messages in the conv.
 */
export const MessageView = memo(MessageViewInner);
