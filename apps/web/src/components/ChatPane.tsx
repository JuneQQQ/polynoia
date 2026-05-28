import { Loader2, PanelRight, Search, Settings, Square } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type ConversationSummary } from "../lib/api";
import { ConvWebSocket } from "../lib/ws";
import { selectAgentStatuses, selectMessages, useStore, type AgentStatusValue } from "../store";
import { AskFormsPanel } from "./AskFormsPanel";
import { Composer } from "./Composer";
import { ConvRolesModal } from "./ConvRolesModal";
import { MessageView } from "./MessageView";
import { PendingEditsPanel } from "./PendingEditsPanel";
import { ConvScopeProvider } from "./parts/_context";

type Props = {
  convId: string;
  members: string[];
  title: string;
};

export function ChatPane({ convId, members, title }: Props) {
  // Fine-grained selectors:
  //   messages: derived ordered list — wrap in useShallow because selectMessages
  //             allocates a new Array every call; without shallow, zustand's
  //             default Object.is detects "changed" on every store update,
  //             forces a re-render, which re-runs the selector → new Array →
  //             ... infinite loop (Zustand v5 + useSyncExternalStore tripwire).
  //   streamTick: primitive number, no useShallow needed.
  //   agentStatuses: Map ref — usually stable (same Map until status changes),
  //             but the empty fallback `new Map()` is unstable; wrap for safety.
  const messages = useStore(useShallow((s) => selectMessages(s, convId)));
  const streamTick = useStore((s) => s.convs.get(convId)?.streamTick ?? 0);
  const agentStatuses = useStore(useShallow((s) => selectAgentStatuses(s, convId)));
  const hasMoreOlder = useStore((s) => s.convs.get(convId)?.hasMoreOlder ?? true);
  const loadingOlder = useStore((s) => s.convs.get(convId)?.loadingOlder ?? false);
  const appendUserMessage = useStore((s) => s.appendUserMessage);
  const appendUserImage = useStore((s) => s.appendUserImage);
  const appendUserFile = useStore((s) => s.appendUserFile);
  const applyChunkToConv = useStore((s) => s.applyChunkToConv);
  const hydrateMessages = useStore((s) => s.hydrateMessages);
  const setLoadingOlder = useStore((s) => s.setLoadingOlder);
  const agents = useStore((s) => s.agents);
  const previewOpen = useStore((s) => s.preview.open);
  const openPreview = useStore((s) => s.openPreview);
  const closePreview = useStore((s) => s.closePreview);

  const wsRef = useRef<ConvWebSocket | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  // Dedupe: drop identical user-message frames sent within 500ms of each
  // other — defensive against double-submit (Strict Mode / accidental
  // double-Enter / Enter+click). Otherwise the agent sees N copies and
  // its native session bloats with phantom turns.
  const lastSentRef = useRef<{ text: string; ts: number } | null>(null);

  // Maintain a WS connection per active conv (lifecycle tied to convId)
  useEffect(() => {
    const ws = new ConvWebSocket(convId);
    wsRef.current = ws;
    ws.onChunk((chunk) => {
      switch (chunk.type) {
        case "message-metadata":
          applyChunkToConv(convId, { kind: "meta", meta: chunk.message_metadata });
          break;
        case "text-start":
          applyChunkToConv(convId, {
            kind: "text-start",
            partId: chunk.id,
            messageId: `msg-${chunk.id}`,
            senderId: chunk.sender_id ?? null,
          });
          break;
        case "text-delta":
          applyChunkToConv(convId, { kind: "text-delta", partId: chunk.id, delta: chunk.delta });
          break;
        case "error":
          // Surface stream-level error as a transient toast-like message
          // (we represent it as a text message from "system" sender).
          applyChunkToConv(convId, {
            kind: "card",
            cardKind: "text",
            payload: {
              kind: "text",
              body: [{ t: "p", c: `⚠️ Error: ${chunk.error_text}` }],
            },
            messageId: `err-${Date.now()}`,
            senderId: "system",
          });
          break;
        case "finish":
        case "start":
        case "start-step":
        case "finish-step":
          // structural chunks — no UI state update needed
          break;
        case "text-end":
          applyChunkToConv(convId, { kind: "text-end", partId: chunk.id });
          break;
        default:
          if (chunk.type === "data-pending-edit") {
            // Manual-mode approval card. Route to pendingEditsByConv —
            // DON'T create a regular message bubble. UI surfaces these as
            // floating ✓/✗ cards above the composer.
            const anyChunk = chunk as any;
            const edit = anyChunk.data;
            if (edit && edit.id && edit.conv_id) {
              useStore.getState().upsertPendingEdit(edit);
            }
          } else if (chunk.type === "data-chain-link") {
            // Transient meta — actual B bubble appears right after A in the
            // stream; this link is redundant UI noise. Silently drop.
          } else if (chunk.type === "data-ask-form") {
            // Agent emitted an <ask-form> block. Route to the floating
            // panel above Composer (NOT into the message stream). User
            // submits inline; answer flows back as a normal user message.
            const anyChunk = chunk as any;
            const af = anyChunk.data;
            if (af && af.id) {
              useStore.getState().enqueueAskForm(convId, af);
            }
          } else if (chunk.type.startsWith("data-")) {
            const cardKind = chunk.type.slice("data-".length);
            const anyChunk = chunk as any;
            const payload = { kind: cardKind, ...anyChunk.data };
            applyChunkToConv(convId, {
              kind: "card",
              cardKind,
              payload,
              messageId: anyChunk.id ?? `card-${Date.now()}`,
              senderId: anyChunk.sender_id ?? null,
            });
          }
      }
    });
    ws.connect().catch((e) => {
      // Filter out the React 18 Strict-Mode double-mount false alarm:
      // when the first mount's effect is unmounted before WS even opens, the
      // promise rejects with an Event whose currentTarget is null. Real
      // errors carry a useful message — log only those.
      if (!e || (typeof e === "object" && (e as Event).currentTarget === null)) return;
      console.error("ws connect failed", e);
    });
    return () => {
      ws.close();
    };
  }, [convId, applyChunkToConv]);

  // ─── Initial history hydration ──────────────────────────────────
  // Without this, refreshing the page wipes the store and the chat looks
  // empty even though messages are persisted in DB. Fetch the newest 50
  // when convId changes; older messages are lazy-loaded via scroll-up
  // sentinel below.
  useEffect(() => {
    let cancelled = false;
    setLoadingOlder(convId, true);
    api
      .convMessages(convId, { limit: 50 })
      .then(({ messages, has_more }) => {
        if (cancelled) return;
        hydrateMessages(convId, messages, { mode: "replace", hasMore: has_more });
      })
      .catch(() => {
        if (!cancelled) setLoadingOlder(convId, false);
      });
    return () => {
      cancelled = true;
    };
  }, [convId, hydrateMessages, setLoadingOlder]);

  // ─── Scroll-up lazy-load older messages ─────────────────────────
  // When user scrolls within 200px of the top AND we have older messages
  // AND no fetch is in flight, pull the next page using the oldest loaded
  // message's timestamp as the cursor. After prepend we restore the
  // scroll offset so the user's view doesn't jump.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const onScroll = async () => {
      if (el.scrollTop > 200) return;
      const { hasMoreOlder, loadingOlder, messages: msgList } = (() => {
        const cs = useStore.getState().convs.get(convId);
        return {
          hasMoreOlder: cs?.hasMoreOlder ?? true,
          loadingOlder: cs?.loadingOlder ?? false,
          messages: cs?.messageOrder ?? [],
        };
      })();
      if (!hasMoreOlder || loadingOlder || msgList.length === 0) return;
      const oldestId = msgList[0];
      const oldestMsg = useStore.getState().convs.get(convId)?.msgById.get(oldestId);
      if (!oldestMsg) return;
      const cursor = oldestMsg.created_at;
      setLoadingOlder(convId, true);
      // Snapshot scroll position before prepend so we can restore offset
      const prevScrollHeight = el.scrollHeight;
      try {
        const { messages: older, has_more } = await api.convMessages(convId, {
          limit: 50,
          before: cursor,
        });
        hydrateMessages(convId, older, { mode: "prepend", hasMore: has_more });
        // After render restore relative scroll so view doesn't jump
        requestAnimationFrame(() => {
          const newScrollHeight = el.scrollHeight;
          el.scrollTop = newScrollHeight - prevScrollHeight;
        });
      } catch {
        setLoadingOlder(convId, false);
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [convId, hydrateMessages, setLoadingOlder]);

  // Auto-scroll — synchronous via useLayoutEffect, fires after DOM mutation
  // but BEFORE paint. This eliminates the throttle/rAF/jitter pattern:
  //   · Earlier we tried throttle 80ms + double rAF — but between scroll
  //     ticks the content grew (text-delta arrives ~50ms), so the last
  //     visible line drifted up off-screen, then the next throttle "jumped"
  //     back to bottom. That's the up-down vibration.
  //   · useLayoutEffect synchronously fires on every streamTick / message
  //     change, reads the just-mutated scrollHeight, writes scrollTop —
  //     all before the browser paints. So the user never sees an
  //     intermediate "almost-bottom" frame.
  // We still RESPECT user scroll-up: if they've scrolled away from the
  // bottom we don't yank them back.
  const wasAtBottomRef = useRef(true);
  // Track user intent: when they scroll up manually, stop auto-following.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const onScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      wasAtBottomRef.current = distFromBottom < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    if (wasAtBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, streamTick]);

  // Listen for "regenerate" events fired by MessageView's action button.
  // The event carries (convId, text) — we filter on convId and resend
  // via this conv's WS. Using a window event avoids threading wsRef
  // through prop drilling.
  useEffect(() => {
    const onRegen = (ev: Event) => {
      const ce = ev as CustomEvent<{ convId: string; text: string }>;
      if (!ce.detail || ce.detail.convId !== convId) return;
      appendUserMessage(convId, ce.detail.text);
      wsRef.current?.sendUserMessage(ce.detail.text, members);
    };
    window.addEventListener("polynoia:regenerate", onRegen);
    return () => window.removeEventListener("polynoia:regenerate", onRegen);
  }, [convId, members, appendUserMessage]);

  const memberAgents = useMemo(
    () => members.filter((m) => m !== "you").map((id) => agents.find((a) => a.id === id)).filter(Boolean),
    [members, agents],
  );
  const isGroup = members.length > 2;

  // Conv summary — needed for workspace_id (gates merge toggle visibility) and
  // for the current merge_mode value. Refetched whenever convId switches.
  const [convSummary, setConvSummary] = useState<ConversationSummary | null>(null);
  useEffect(() => {
    let alive = true;
    setConvSummary(null);
    api.getConv(convId).then((c) => {
      if (!alive) return;
      setConvSummary(c);
      // Push workspaceId into the preview state so Web/Code tabs can load
      // the right workspace's files when the user opens the right rail.
      useStore.setState((s) => ({
        preview: {
          ...s.preview,
          data: { ...s.preview.data, workspaceId: c.workspace_id },
        },
      }));
    }).catch(() => {});
    return () => { alive = false; };
  }, [convId]);
  const mergeMode = convSummary?.merge_mode ?? "auto";
  const inWorkspace = !!convSummary?.workspace_id;

  const [rolesModalOpen, setRolesModalOpen] = useState(false);

  const toggleMergeMode = async () => {
    if (!convSummary) return;
    const next: "auto" | "manual" = mergeMode === "auto" ? "manual" : "auto";
    // Optimistic flip — server PATCH returns canonical state.
    setConvSummary({ ...convSummary, merge_mode: next });
    try {
      const updated = await api.setMergeMode(convId, next);
      setConvSummary(updated);
    } catch {
      // Roll back on failure
      setConvSummary({ ...convSummary, merge_mode: mergeMode });
    }
  };

  // List of agents currently doing work (starting/streaming) — for the status row
  const activeAgents = useMemo(() => {
    const out: { id: string; status: AgentStatusValue; message?: string }[] = [];
    for (const [id, st] of agentStatuses) {
      if (st.status === "starting" || st.status === "streaming") {
        out.push({ id, status: st.status, message: st.message });
      }
    }
    return out;
  }, [agentStatuses]);

  return (
    <main className="flex-1 flex flex-col min-w-0 bg-[var(--color-bg)]">
      {/* Chat header — editorial: serif title + hair-line meta, no chunky pill */}
      <header className="relative flex items-center gap-3 px-6 py-3 bg-[var(--color-surface)]">
        <span
          aria-hidden
          className="absolute left-0 right-0 bottom-0 h-px bg-[var(--color-line)]"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2.5">
            <span className="font-display text-[16px] font-medium truncate text-[var(--color-fg)] tracking-wide">
              {title}
            </span>
            {isGroup && (
              <button
                type="button"
                onClick={() => useStore.getState().openMembersList()}
                className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
                title="查看成员"
              >
                {memberAgents.length + 1} 成员
              </button>
            )}
          </div>
          <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
            {isGroup ? (
              <>
                主 Agent · <b className="text-[var(--color-purple)] font-medium">Orchestrator</b>
              </>
            ) : (
              memberAgents[0]?.tagline ?? "Agent"
            )}
          </div>
        </div>
        <div className="flex -space-x-1.5">
          {memberAgents.slice(0, 5).map(
            (a) =>
              a && (
                <button
                  type="button"
                  key={a.id}
                  onClick={() => useStore.getState().openAgentDetail(a.id)}
                  className="w-7 h-7 rounded-full grid place-items-center text-white text-[10px] font-medium border-2 border-[var(--color-surface)] transition-all duration-200 hover:scale-[1.12] hover:shadow-md hover:z-10"
                  style={{ background: a.color }}
                  title={`查看 ${a.name} 详情`}
                >
                  {a.initials}
                </button>
              ),
          )}
        </div>
        {inWorkspace && (
          <div
            className="ml-3 flex items-stretch rounded-md border border-[var(--color-line)] overflow-hidden text-[11px] font-mono uppercase tracking-[0.18em] font-medium"
            role="group"
            aria-label="merge mode"
          >
            <button
              type="button"
              onClick={() => mergeMode !== "auto" && toggleMergeMode()}
              className={`px-3.5 py-1.5 transition-colors duration-150 ${
                mergeMode === "auto"
                  ? "bg-[var(--color-accent)] text-white"
                  : "bg-transparent text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
              }`}
              title="Auto · 子任务完成后 Orchestrator 自动合并到 main"
            >
              Auto
            </button>
            <button
              type="button"
              onClick={() => mergeMode !== "manual" && toggleMergeMode()}
              className={`px-3.5 py-1.5 transition-colors duration-150 border-l border-[var(--color-line)] ${
                mergeMode === "manual"
                  ? "bg-[var(--color-accent)] text-white"
                  : "bg-transparent text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
              }`}
              title="Manual · 每个 edit 都需要你点确认才落盘"
            >
              Manual
            </button>
          </div>
        )}
        <div className="flex items-center gap-1 ml-2">
          <button
            type="button"
            onClick={() => useStore.getState().setSearchOverlayOpen(true)}
            className="p-1.5 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
            title="搜索 (⌘K / Ctrl+K)"
          >
            <Search size={14} />
          </button>
          <button
            type="button"
            onClick={() => (previewOpen ? closePreview() : openPreview("web"))}
            className={`p-1.5 rounded transition ${
              previewOpen
                ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                : "hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
            }`}
            title="产物面板"
          >
            <PanelRight size={14} />
          </button>
          <button
            type="button"
            onClick={() => isGroup && setRolesModalOpen(true)}
            disabled={!isGroup || !convSummary}
            className={`p-1.5 rounded transition ${
              isGroup
                ? "hover:bg-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
                : "text-[var(--color-fg-4)] opacity-40 cursor-not-allowed"
            }`}
            title={isGroup ? "成员角色" : "仅群聊可编辑角色"}
          >
            <Settings size={14} />
          </button>
        </div>
      </header>

      {/* Message stream — relative wrapper so the "running" status pill can
          float on top without displacing content. */}
      <div className="flex-1 min-h-0 relative">
        {/* Per-agent live status pill — floats over the top of the message
            area when ≥1 agent is working. NOT in the normal flow so it doesn't
            push the message list down when streaming starts / yank back up
            when it ends. */}
        {activeAgents.length > 0 && (
          <div className="anim-fade-up pointer-events-none absolute top-2 left-1/2 -translate-x-1/2 z-20 flex flex-wrap items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--color-surface)]/90 backdrop-blur-sm border border-[var(--color-line)] shadow-sm text-[11.5px]">
            <span className="text-[var(--color-fg-3)] pointer-events-none">运行中</span>
            {activeAgents.map((a) => {
              const agent = agents.find((x) => x.id === a.id);
              return (
                <button
                  type="button"
                  key={a.id}
                  onClick={() => wsRef.current?.abort(a.id)}
                  className="group pointer-events-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-[var(--color-line)] hover:bg-[var(--color-red-soft)] hover:border-[var(--color-red)] transition"
                  title={`点击中断 ${agent?.name ?? a.id}`}
                  style={{ background: agent?.bg ?? "var(--color-line)" }}
                >
                  <Loader2 size={10} className="animate-spin" style={{ color: agent?.color ?? "#666" }} />
                  <span style={{ color: agent?.color ?? "var(--color-fg-2)" }}>{agent?.name ?? a.id}</span>
                  <Square
                    size={10}
                    className="ml-0.5 opacity-0 group-hover:opacity-100 transition"
                    style={{ color: "var(--color-red)" }}
                  />
                </button>
              );
            })}
            {activeAgents.length > 1 && (
              <button
                type="button"
                onClick={() => wsRef.current?.abort()}
                className="pointer-events-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[var(--color-red)] hover:bg-[var(--color-red-soft)] transition"
                title="全部中断"
              >
                <Square size={10} /> 全部停止
              </button>
            )}
          </div>
        )}

      <ConvScopeProvider value={{ convId, inWorkspace }}>
      <div ref={bodyRef} className="absolute inset-0 overflow-y-auto py-4">
        {/* Lazy-load top sentinel — visible spinner while older messages
            are being fetched. Shown only if we have more to fetch. */}
        {loadingOlder && messages.length > 0 && (
          <div className="flex items-center justify-center gap-2 py-3 text-[11px] text-[var(--color-fg-3)]">
            <Loader2 size={11} className="animate-spin" />
            加载更早的消息…
          </div>
        )}
        {!hasMoreOlder && messages.length > 10 && (
          <div className="flex items-center justify-center gap-2 py-2 text-[10.5px] text-[var(--color-fg-4)]">
            <span className="h-px w-12 bg-[var(--color-line)]" />
            <span>对话的开始</span>
            <span className="h-px w-12 bg-[var(--color-line)]" />
          </div>
        )}
        {!messages.length && (
          <div className="text-center text-[var(--color-fg-3)] text-[12px] py-12">
            还没有消息 · 试试发送一条
            <div className="mt-3 text-[11px] text-[var(--color-fg-4)]">
              输入 <span className="px-1 py-0.5 rounded bg-[var(--color-surface-2)]">@</span> 可
              选择特定 agent 直接对话
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          // Group consecutive same-sender messages into one visual bubble:
          // avatar + name + timestamp only on the FIRST message of a run.
          // Tool calls + text from the same agent turn (which arrive as
          // separate messages in the store because of distinct message_ids)
          // now visually read as one continuous reply.
          const prev = i > 0 ? messages[i - 1] : null;
          const isGrouped = !!prev && prev.sender_id === m.sender_id;
          return (
            <MessageView
              key={m.id}
              convId={convId}
              msgId={m.id}
              isGrouped={isGrouped}
            />
          );
        })}
      </div>
      </ConvScopeProvider>
      </div>

      {/* Agent-initiated questions — floating panel above Composer */}
      <AskFormsPanel convId={convId} members={members} ws={wsRef.current} />

      {/* Manual-mode pending edits — sits above Composer */}
      <PendingEditsPanel convId={convId} />

      {/* Composer */}
      <Composer
        convId={convId}
        members={members}
        onAttachImage={(img) => {
          // Optimistic UI append + fire-and-forget persistence. Data URL
          // is currently inlined in the row's payload column (sqlite JSON);
          // P1+ upgrade to upload endpoint returning short URL.
          appendUserImage(convId, {
            src: img.src,
            name: img.name,
            media_type: img.media_type,
          });
          api.createMessage({
            conv_id: convId,
            payload: {
              kind: "image",
              src: img.src,
              name: img.name ?? null,
              media_type: img.media_type ?? null,
            },
          }).catch(() => {
            /* image survives session even if persist fails — acceptable */
          });
        }}
        onAttachFile={(file) => {
          appendUserFile(convId, {
            src: file.src,
            name: file.name,
            media_type: file.media_type,
            size_bytes: file.size_bytes,
          });
          api.createMessage({
            conv_id: convId,
            payload: {
              kind: "file",
              src: file.src,
              name: file.name,
              media_type: file.media_type ?? null,
              size_bytes: file.size_bytes ?? null,
            },
          }).catch(() => {
            /* file survives session even if persist fails */
          });
        }}
        onSend={(text, inReplyTo) => {
          const now = Date.now();
          const last = lastSentRef.current;
          if (last && last.text === text && now - last.ts < 500) {
            // Same text within 500ms — drop. Symptom: duplicate "你好" bubbles
            // and agent counting phantom turns. Root cause TBD (Strict Mode
            // / accidental double-input); this is the user-visible bandage.
            return;
          }
          lastSentRef.current = { text, ts: now };
          appendUserMessage(convId, text, inReplyTo);
          wsRef.current?.sendUserMessage(text, members, inReplyTo);
        }}
      />
      {rolesModalOpen && convSummary && (
        <ConvRolesModal
          conv={convSummary}
          onClose={() => setRolesModalOpen(false)}
          onSaved={(updated) => setConvSummary(updated)}
        />
      )}
    </main>
  );
}
