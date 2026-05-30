/** AskFormPart — inline record of an agent's `<ask-form>` question inside the
 * message bubble.
 *
 * This is a READ-ONLY record, NOT a second answer surface — you answer in the
 * floating AskFormsPanel above the Composer (which sends + dequeues). The bug
 * this fixes: the inline form used to keep purely-local `submitted`/`answers`
 * state, so after you answered (in the panel) it still showed "需要你回复" and
 * re-prompted, losing your pick. Now it derives the answered-state + the picked
 * option from the persisted conversation — an ask-form counts as answered once
 * any `you` message follows it (the same rule the server uses) — so it survives
 * refresh and echoes your choice.
 */
import { Check, MessageCircleQuestion } from "lucide-react";
import { useMemo } from "react";
import type { AskFormPayload, MessagePayload } from "../../lib/types";
import { useStore } from "../../store";

function extractText(payload: MessagePayload): string {
  if (payload.kind !== "text") return "";
  return payload.body
    .map((b) =>
      typeof b.c === "string"
        ? b.c
        : b.c.map((seg) => (seg.type === "text" ? seg.text : "")).join(""),
    )
    .join(" ");
}

export function AskFormPart({
  payload,
  convId,
  msgId,
}: {
  payload: AskFormPayload;
  convId?: string;
  msgId?: string;
}) {
  // Answered ⟺ a `you` message follows this ask-form in the conversation.
  // Capture that reply's text so we can echo / highlight the chosen option.
  const answerText = useStore((s) => {
    if (!convId || !msgId) return null;
    const conv = s.convs.get(convId);
    if (!conv) return null;
    const idx = conv.messageOrder.indexOf(msgId);
    if (idx < 0) return null;
    for (let i = idx + 1; i < conv.messageOrder.length; i++) {
      const m = conv.msgById.get(conv.messageOrder[i]);
      if (m && m.sender_id === "you") return extractText(m.payload);
    }
    return null;
  });
  const answered = answerText != null;

  // Best-effort: recover each single-choice pick by matching the option label
  // inside the reply text (the panel formats answers as "… · {opt.label} · …").
  const picks = useMemo(() => {
    const out: Record<string, string> = {};
    if (!answerText) return out;
    for (const q of payload.questions) {
      if (q.kind === "single" && q.options) {
        const hit = q.options.find((o) => o.label && answerText.includes(o.label));
        if (hit) out[q.id] = hit.value;
      }
    }
    return out;
  }, [answerText, payload.questions]);

  return (
    <div
      className={`border-2 rounded-xl overflow-hidden bg-[var(--color-surface)] max-w-[640px] transition ${
        answered ? "border-[var(--color-green)]/40" : "border-[var(--color-accent)]/50"
      }`}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2"
        style={{
          background: answered ? "var(--color-green-soft)" : "var(--color-accent-soft)",
        }}
      >
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-semibold uppercase tracking-wider"
          style={{
            background: answered ? "var(--color-green)" : "var(--color-accent)",
            color: "#fff",
          }}
        >
          {answered ? (
            <Check size={11} strokeWidth={3} />
          ) : (
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
          )}
          {answered ? "已回复" : "需要你回复"}
        </span>
        <span className="text-xs font-medium flex-1 truncate">
          {payload.title || "Agent 需要确认"}
        </span>
      </div>

      {/* Body — read-only record */}
      <div className="p-3 space-y-3">
        {payload.questions.map((q, qi) => {
          const picked = picks[q.id];
          return (
            <div key={q.id}>
              <div className="flex items-start gap-2 mb-1.5">
                <span
                  className="w-5 h-5 rounded-full grid place-items-center text-[10px] font-bold flex-shrink-0 mt-0.5"
                  style={{
                    background: "var(--color-accent-soft)",
                    color: "var(--color-accent)",
                  }}
                >
                  {qi + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px] font-medium">{q.label}</div>
                  {q.sub && (
                    <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
                      {q.sub}
                    </div>
                  )}
                </div>
              </div>

              {q.kind === "single" && q.options && (
                <div className="space-y-1.5 pl-7">
                  {q.options.map((opt) => {
                    const isPick = picked === opt.value;
                    return (
                      <div
                        key={opt.value}
                        className={`flex items-start gap-2 px-2.5 py-2 rounded-md border transition ${
                          isPick
                            ? "bg-[var(--color-green-soft)] border-[var(--color-green)]"
                            : answered
                              ? "border-[var(--color-line)] opacity-40"
                              : "border-[var(--color-line)]"
                        }`}
                      >
                        <span
                          className="w-4 h-4 mt-0.5 rounded-full border-[1.5px] grid place-items-center flex-shrink-0"
                          style={{
                            borderColor: isPick
                              ? "var(--color-green)"
                              : "var(--color-line-strong)",
                            background: isPick ? "var(--color-green)" : "transparent",
                          }}
                        >
                          {isPick && <Check size={10} color="#fff" strokeWidth={3} />}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-[12px] flex items-center gap-1.5">
                            <span className="font-medium">{opt.label}</span>
                            {isPick && (
                              <span
                                className="text-[9.5px] uppercase tracking-wider font-semibold"
                                style={{ color: "var(--color-green)" }}
                              >
                                你的选择
                              </span>
                            )}
                          </div>
                          {opt.desc && (
                            <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
                              {opt.desc}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {q.kind === "multi" && q.options && (
                <div className="pl-7 text-[11px] text-[var(--color-fg-3)]">
                  {q.options.map((o) => o.label).join(" / ")}
                </div>
              )}
              {q.kind === "fill" && q.placeholder && (
                <div className="pl-7 text-[11px] text-[var(--color-fg-4)] italic">
                  {q.placeholder}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px]">
        {answered ? (
          <span
            className="inline-flex items-center gap-1.5 font-medium min-w-0"
            style={{ color: "var(--color-green)" }}
          >
            <Check size={11} strokeWidth={3} className="flex-shrink-0" />
            <span className="truncate">已回复：{answerText}</span>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[var(--color-fg-3)]">
            <MessageCircleQuestion size={12} className="text-[var(--color-accent)] flex-shrink-0" />
            请在下方「Awaiting your input」面板作答
          </span>
        )}
      </div>
    </div>
  );
}
