/** AskFormsPanel — floating panel above Composer for agent-initiated
 * questions (`<ask-form>` blocks).
 *
 * Mirrors PendingEditsPanel's UX (left orange stripe, mono caps eyebrow,
 * card stack). Renders ONE active ask at a time; others queue dimmed.
 * On submit:formats answers as a readable text reply and sends via WS
 * sendUserMessage so the agent that asked sees the answer in next turn's
 * L4 history.
 */
import { Check, ChevronDown, ChevronUp, MessageCircleQuestion, Send } from "lucide-react";
import { useEffect } from "react";
import { useState } from "react";
import { api } from "../lib/api";
import { useStore, type AskFormEntry } from "../store";
import { ConvWebSocket } from "../lib/ws";

type Props = {
  convId: string;
  members: string[];
  ws: ConvWebSocket | null;
};

export function AskFormsPanel({ convId, members, ws }: Props) {
  const list = useStore((s) => s.askFormsByConv.get(convId) ?? EMPTY);
  const dequeue = useStore((s) => s.dequeueAskForm);
  const enqueue = useStore((s) => s.enqueueAskForm);
  const appendUserMessage = useStore((s) => s.appendUserMessage);
  const agents = useStore((s) => s.agents);

  // Re-hydrate still-open ask-forms after a refresh (the live data-ask-form
  // chunk is gone, but the question was persisted). Dedup against whatever is
  // already queued from the live stream.
  useEffect(() => {
    let alive = true;
    api.openAskForms(convId)
      .then((res) => {
        if (!alive) return;
        const present = new Set(
          (useStore.getState().askFormsByConv.get(convId) ?? []).map((f) => f.id),
        );
        for (const af of res.ask_forms) {
          if (!present.has(af.id)) enqueue(convId, af as unknown as AskFormEntry);
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [convId, enqueue]);

  const [collapsed, setCollapsed] = useState(false);

  if (list.length === 0) return null;
  const [active, ...queued] = list;

  const onAnswered = (af: AskFormEntry, answerText: string) => {
    // 1) Render the answer as a user message (so it shows in the chat either way).
    // Capture the id so the WS path can echo-dedup against this optimistic bubble.
    const ansId = appendUserMessage(convId, answerText);
    if (af.blocking_tool) {
      // ⑥ Blocking `ask_user` tool: resolve the SUSPENDED agent turn — it
      // continues with this answer. No new user message / WS turn needed.
      api.answerAsk(convId, af.id, answerText).catch(() => {});
    } else {
      // Legacy <ask-form> text path: send via WS so the asking agent's NEXT
      // turn sees it. In a group, @-address the asker so the conv routes back;
      // in a 1:1 (单聊, ≤2 members) there's no one else to route to — no @.
      const asker = agents.find((a) => a.id === af.agent_id);
      const isDM = members.length <= 2;
      const tagged = asker && !isDM ? `@${asker.name} ${answerText}` : answerText;
      ws?.sendUserMessage(tagged, members, undefined, ansId);
    }
    // 3) Drop the card
    dequeue(convId, af.id);
  };

  return (
    <div className="px-6 pt-2 pb-2 border-t border-[var(--color-line)] bg-[var(--color-accent-soft)]/20">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-2 py-0.5 text-[10.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-accent)] font-medium hover:opacity-80"
      >
        <MessageCircleQuestion size={11} />
        <span>Awaiting your input · {list.length}</span>
        <span className="ml-auto inline-flex items-center gap-0.5 normal-case tracking-normal text-[var(--color-fg-3)]">
          {collapsed ? "展开作答" : "收起"}
          {collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </span>
      </button>
      {!collapsed && (
        <div className="mt-2 space-y-2 max-h-[30vh] overflow-y-auto pr-1">
          <AskCard af={active} agents={agents} onAnswered={onAnswered} active />
          {queued.length > 0 && (
            <>
              <div className="flex items-center gap-2 mt-3 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)]">
                <span className="h-px flex-1 bg-[var(--color-line)]" />
                <span>Queued · {queued.length}</span>
                <span className="h-px flex-1 bg-[var(--color-line)]" />
              </div>
              {queued.map((af) => (
                <AskCard
                  key={af.id}
                  af={af}
                  agents={agents}
                  onAnswered={onAnswered}
                  active={false}
                />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

const EMPTY: readonly AskFormEntry[] = [];

// Sentinel option for the user-supplied 「其他」 free-text choice on single/multi
// questions — so the user is never boxed into the agent's preset options.
const OTHER = "__pn_other__";

function AskCard({
  af,
  agents,
  onAnswered,
  active,
}: {
  af: AskFormEntry;
  agents: { id: string; name: string; color: string; initials: string }[];
  onAnswered: (af: AskFormEntry, answer: string) => void;
  active: boolean;
}) {
  // Per-question answer state, keyed by q.id
  const [answers, setAnswers] = useState<Record<string, string | string[]>>(() => {
    const init: Record<string, string | string[]> = {};
    for (const q of af.questions) {
      if (q.kind === "multi") init[q.id] = [];
      else init[q.id] = "";
    }
    return init;
  });
  // Free-text for the 「其他」 choice, keyed by q.id.
  const [otherText, setOtherText] = useState<Record<string, string>>({});
  // 「这问题不够清楚 · 让它展开说」 — instead of answering, bounce a free-form
  // clarification request back to the asking agent; it re-asks with more detail.
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyText, setClarifyText] = useState(
    "你的问题对我来说不够清楚——请把背景、每个选项的含义、以及你到底要我定哪个点,都展开讲清楚,然后再问我一次。",
  );

  const asker = agents.find((a) => a.id === af.agent_id);

  const setSingle = (qid: string, v: string) =>
    setAnswers((a) => ({ ...a, [qid]: v }));
  const setFill = (qid: string, v: string) =>
    setAnswers((a) => ({ ...a, [qid]: v }));
  const setOther = (qid: string, v: string) =>
    setOtherText((o) => ({ ...o, [qid]: v }));
  const toggleMulti = (qid: string, v: string) => {
    setAnswers((a) => {
      const cur = new Set((a[qid] as string[]) ?? []);
      cur.has(v) ? cur.delete(v) : cur.add(v);
      return { ...a, [qid]: [...cur] };
    });
  };

  const isAnswered = af.questions.every((q) => {
    if (q.optional) return true;
    // Free-text 补充说明 is inherently optional — never block submit on a fill.
    if (q.kind === "fill") return true;
    const v = answers[q.id];
    if (q.kind === "single") {
      // 「其他」 selected → require the custom text.
      if (v === OTHER) return (otherText[q.id] ?? "").trim().length > 0;
      return typeof v === "string" && v.length > 0;
    }
    if (q.kind === "multi") {
      const arr = Array.isArray(v) ? v : [];
      if (arr.includes(OTHER) && !(otherText[q.id] ?? "").trim()) return false;
      return arr.length > 0;
    }
    return true;
  });

  const submit = () => {
    if (!isAnswered || !active) return;
    // Format answers as compact readable text:
    //   "v1.0 范围澄清: 主要面向? · Python 开发者 · slogan? · Compose AI agents..."
    const parts: string[] = [];
    if (af.title) parts.push(af.title + ":");
    for (const q of af.questions) {
      const v = answers[q.id];
      if (q.kind === "single") {
        if (v === OTHER) {
          parts.push(`${q.label} · ${otherText[q.id] || "(其他)"}`);
        } else {
          const opt = q.options?.find((o) => o.value === v);
          parts.push(`${q.label} · ${opt?.label ?? v}`);
        }
      } else if (q.kind === "multi") {
        const labels = (v as string[]).map((vv) =>
          vv === OTHER
            ? otherText[q.id] || "(其他)"
            : (q.options?.find((o) => o.value === vv)?.label ?? vv),
        );
        parts.push(`${q.label} · ${labels.join(" + ")}`);
      } else {
        parts.push(`${q.label} · ${v || "(未填)"}`);
      }
    }
    onAnswered(af, parts.join(" · "));
  };

  // ④ Bounce the question back asking the agent to clarify (chat about it).
  const sendClarify = () => {
    const t = clarifyText.trim();
    if (!t || !active) return;
    onAnswered(af, t);
  };

  return (
    <div
      className={`relative bg-[var(--color-surface)] rounded-md overflow-hidden border border-[var(--color-line)] ${
        active ? "" : "opacity-50"
      }`}
    >
      {/* 4px left accent stripe */}
      <span
        aria-hidden
        className="absolute left-0 top-0 bottom-0 w-[4px]"
        style={{ background: "var(--color-accent)" }}
      />
      {/* Header */}
      <div className="flex items-center gap-2 pl-4 pr-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <MessageCircleQuestion size={12} className="text-[var(--color-accent)]" />
        <span className="font-display text-[13px] text-[var(--color-fg)] truncate flex-1">
          {af.title || "Agent needs input"}
        </span>
        {asker && (
          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-[var(--color-fg-2)]">
            <span
              className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
              style={{ background: asker.color }}
            >
              {asker.initials}
            </span>
            <span>{asker.name}</span>
          </span>
        )}
      </div>

      {/* Questions */}
      <div className="pl-4 pr-3 py-2.5 space-y-2.5">
        {af.questions.map((q, qi) => (
          <div key={q.id}>
            <div className="flex items-baseline gap-2 mb-1.5">
              <span className="w-5 h-5 rounded-full grid place-items-center text-[10px] font-medium bg-[var(--color-accent-soft)] text-[var(--color-accent)] flex-shrink-0">
                {qi + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[12.5px] font-medium text-[var(--color-fg)] flex items-center gap-1.5">
                  {q.label}
                  {q.optional && (
                    <span className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)]">
                      Optional
                    </span>
                  )}
                </div>
                {q.sub && (
                  <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
                    {q.sub}
                  </div>
                )}
              </div>
            </div>
            <div className="pl-7 space-y-1.5">
              {q.kind === "single" && q.options?.map((opt) => {
                const picked = answers[q.id] === opt.value;
                return (
                  <button
                    type="button"
                    key={opt.value}
                    onClick={() => active && setSingle(q.id, opt.value)}
                    disabled={!active}
                    className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
                      picked
                        ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                        : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                    }`}
                  >
                    <span
                      className="w-3.5 h-3.5 rounded-full border-[1.5px] mt-0.5 flex-shrink-0"
                      style={{
                        borderColor: picked ? "var(--color-accent)" : "var(--color-line-strong)",
                        background: picked ? "var(--color-accent)" : "transparent",
                      }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-[12px] font-medium text-[var(--color-fg)]">
                        {opt.label}
                      </div>
                      {opt.desc && (
                        <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
                          {opt.desc}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
              {q.kind === "single" && (
                <button
                  type="button"
                  onClick={() => active && setSingle(q.id, OTHER)}
                  disabled={!active}
                  className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
                    answers[q.id] === OTHER
                      ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                      : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                  }`}
                >
                  <span
                    className="w-3.5 h-3.5 rounded-full border-[1.5px] mt-0.5 flex-shrink-0"
                    style={{
                      borderColor:
                        answers[q.id] === OTHER ? "var(--color-accent)" : "var(--color-line-strong)",
                      background: answers[q.id] === OTHER ? "var(--color-accent)" : "transparent",
                    }}
                  />
                  <div className="flex-1 min-w-0 text-[12px] font-medium text-[var(--color-fg)]">
                    其他(自己填)
                  </div>
                </button>
              )}
              {q.kind === "single" && answers[q.id] === OTHER && (
                <textarea
                  value={otherText[q.id] ?? ""}
                  onChange={(e) => setOther(q.id, e.target.value)}
                  placeholder="输入你的答案…"
                  rows={2}
                  disabled={!active}
                  className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-accent)]/60 bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
                />
              )}
              {q.kind === "multi" && q.options?.map((opt) => {
                const picked = ((answers[q.id] as string[]) ?? []).includes(opt.value);
                return (
                  <button
                    type="button"
                    key={opt.value}
                    onClick={() => active && toggleMulti(q.id, opt.value)}
                    disabled={!active}
                    className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
                      picked
                        ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                        : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                    }`}
                  >
                    <span
                      className="w-3.5 h-3.5 rounded-[3px] border-[1.5px] grid place-items-center mt-0.5 flex-shrink-0"
                      style={{
                        borderColor: picked ? "var(--color-accent)" : "var(--color-line-strong)",
                        background: picked ? "var(--color-accent)" : "transparent",
                      }}
                    >
                      {picked && <Check size={9} color="#fff" strokeWidth={3} />}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-[12px] font-medium text-[var(--color-fg)]">
                        {opt.label}
                      </div>
                      {opt.desc && (
                        <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
                          {opt.desc}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
              {q.kind === "multi" && (
                <button
                  type="button"
                  onClick={() => active && toggleMulti(q.id, OTHER)}
                  disabled={!active}
                  className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
                    ((answers[q.id] as string[]) ?? []).includes(OTHER)
                      ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                      : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                  }`}
                >
                  <span
                    className="w-3.5 h-3.5 rounded-[3px] border-[1.5px] grid place-items-center mt-0.5 flex-shrink-0"
                    style={{
                      borderColor: ((answers[q.id] as string[]) ?? []).includes(OTHER)
                        ? "var(--color-accent)"
                        : "var(--color-line-strong)",
                      background: ((answers[q.id] as string[]) ?? []).includes(OTHER)
                        ? "var(--color-accent)"
                        : "transparent",
                    }}
                  >
                    {((answers[q.id] as string[]) ?? []).includes(OTHER) && (
                      <Check size={9} color="#fff" strokeWidth={3} />
                    )}
                  </span>
                  <div className="flex-1 min-w-0 text-[12px] font-medium text-[var(--color-fg)]">
                    其他(自己填)
                  </div>
                </button>
              )}
              {q.kind === "multi" && ((answers[q.id] as string[]) ?? []).includes(OTHER) && (
                <textarea
                  value={otherText[q.id] ?? ""}
                  onChange={(e) => setOther(q.id, e.target.value)}
                  placeholder="输入你的答案…"
                  rows={2}
                  disabled={!active}
                  className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-accent)]/60 bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
                />
              )}
              {q.kind === "fill" && (
                <textarea
                  value={(answers[q.id] as string) ?? ""}
                  onChange={(e) => setFill(q.id, e.target.value)}
                  placeholder={q.placeholder ?? ""}
                  rows={2}
                  disabled={!active}
                  className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
                />
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="pl-4 pr-3 py-2.5 bg-[var(--color-surface-2)] border-t border-[var(--color-line)] space-y-2">
        {clarifyOpen && active && (
          <textarea
            value={clarifyText}
            onChange={(e) => setClarifyText(e.target.value)}
            rows={3}
            placeholder="告诉它问题哪里不清楚、你想让它展开什么…"
            className="w-full px-2.5 py-2 text-[12px] rounded-md border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
          />
        )}
        <div className="flex items-center gap-2">
          {clarifyOpen ? (
            <>
              <button
                type="button"
                onClick={sendClarify}
                disabled={!clarifyText.trim() || !active}
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Send size={12} /> 发送追问
              </button>
              <button
                type="button"
                onClick={() => setClarifyOpen(false)}
                className="text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
              >
                取消
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={submit}
                disabled={!isAnswered || !active}
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Send size={12} /> Send answer
              </button>
              {active && (
                <button
                  type="button"
                  onClick={() => setClarifyOpen(true)}
                  className="ml-auto inline-flex items-center gap-1 text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
                  title="把这个问题打回去,让 Agent 把背景和选项讲清楚再问"
                >
                  <MessageCircleQuestion size={12} />
                  问题不清楚?让它展开说
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
