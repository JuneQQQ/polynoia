import { Check, Send } from "lucide-react";
import { useMemo, useState } from "react";
import type { AskFormPayload, AskQuestion } from "../../lib/types";

type Answers = Record<string, string | string[] | null>;

function isAnswered(q: AskQuestion, v: Answers[string]): boolean {
  if (q.optional) return true;
  if (q.kind === "single") return v != null;
  if (q.kind === "multi") return Array.isArray(v) && v.length > 0;
  if (q.kind === "fill") return typeof v === "string" && v.trim().length > 0;
  return true;
}

export function AskFormPart({ payload }: { payload: AskFormPayload }) {
  const [answers, setAnswers] = useState<Answers>(() => {
    const init: Answers = {};
    for (const q of payload.questions) {
      if (q.default_value !== undefined && q.default_value !== null) {
        init[q.id] = q.default_value as any;
      } else if (q.kind === "multi") init[q.id] = [];
      else init[q.id] = q.kind === "fill" ? "" : null;
    }
    return init;
  });
  const [submitted, setSubmitted] = useState(false);

  const requiredCount = useMemo(
    () => payload.questions.filter((q) => !q.optional).length,
    [payload.questions],
  );

  const allRequiredAnswered = useMemo(
    () => payload.questions.every((q) => isAnswered(q, answers[q.id])),
    [answers, payload.questions],
  );

  const setSingle = (qid: string, val: string) =>
    setAnswers((a) => ({ ...a, [qid]: val }));
  const toggleMulti = (qid: string, val: string) =>
    setAnswers((a) => {
      const cur = new Set((a[qid] as string[]) ?? []);
      cur.has(val) ? cur.delete(val) : cur.add(val);
      return { ...a, [qid]: [...cur] };
    });
  const setFill = (qid: string, val: string) =>
    setAnswers((a) => ({ ...a, [qid]: val }));

  return (
    <div
      className={`border-2 rounded-xl overflow-hidden bg-[var(--color-surface)] max-w-[640px] transition ${
        submitted ? "border-[var(--color-green)]/40" : "border-[var(--color-accent)]/50"
      }`}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2"
        style={{
          background: submitted
            ? "var(--color-green-soft)"
            : "var(--color-accent-soft)",
        }}
      >
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-semibold uppercase tracking-wider"
          style={{
            background: submitted ? "var(--color-green)" : "var(--color-accent)",
            color: "#fff",
          }}
        >
          {!submitted && (
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
          )}
          {submitted ? "已回复" : "需要你回复"}
        </span>
        <span className="text-xs font-medium flex-1 truncate">
          {payload.title || "Agent 需要确认"}
        </span>
        {!submitted && (
          <span className="text-[10.5px] text-[var(--color-fg-3)]">
            {requiredCount} 必答
            {payload.questions.length > requiredCount &&
              ` · ${payload.questions.length - requiredCount} 可选`}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="p-3 space-y-3">
        {payload.questions.map((q, qi) => {
          const v = answers[q.id];
          return (
            <div key={q.id} className={submitted ? "opacity-70 pointer-events-none" : ""}>
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
                  <div className="text-[12.5px] font-medium flex items-center gap-1.5">
                    {q.label}
                    {q.optional && (
                      <span className="text-[9.5px] text-[var(--color-fg-4)] uppercase tracking-wider">
                        可选
                      </span>
                    )}
                  </div>
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
                    const picked = v === opt.value;
                    return (
                      <label
                        key={opt.value}
                        className={`flex items-start gap-2 px-2.5 py-2 rounded-md cursor-pointer border ${
                          picked
                            ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                            : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                        }`}
                        onClick={() => !submitted && setSingle(q.id, opt.value)}
                      >
                        <span
                          className="w-4 h-4 mt-0.5 rounded-full border-[1.5px] flex-shrink-0"
                          style={{
                            borderColor: picked
                              ? "var(--color-accent)"
                              : "var(--color-line-strong)",
                            background: picked ? "var(--color-accent)" : "transparent",
                          }}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-[12px] flex items-center gap-1.5">
                            <span className="font-medium">{opt.label}</span>
                            {opt.tag && (
                              <span
                                className="text-[10px] px-1.5 py-0 rounded-full"
                                style={{
                                  background: "var(--color-accent-soft)",
                                  color: "var(--color-accent)",
                                }}
                              >
                                {opt.tag}
                              </span>
                            )}
                          </div>
                          {opt.desc && (
                            <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
                              {opt.desc}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}

              {q.kind === "multi" && q.options && (
                <div className="space-y-1.5 pl-7">
                  {q.options.map((opt) => {
                    const arr = (v as string[]) ?? [];
                    const picked = arr.includes(opt.value);
                    return (
                      <label
                        key={opt.value}
                        className={`flex items-start gap-2 px-2.5 py-2 rounded-md cursor-pointer border ${
                          picked
                            ? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
                            : "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                        }`}
                        onClick={() => !submitted && toggleMulti(q.id, opt.value)}
                      >
                        <span
                          className="w-4 h-4 mt-0.5 rounded-[3px] border-[1.5px] grid place-items-center flex-shrink-0"
                          style={{
                            borderColor: picked
                              ? "var(--color-accent)"
                              : "var(--color-line-strong)",
                            background: picked ? "var(--color-accent)" : "transparent",
                          }}
                        >
                          {picked && <Check size={10} color="#fff" strokeWidth={3} />}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-[12px] font-medium">{opt.label}</div>
                          {opt.desc && (
                            <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
                              {opt.desc}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}

              {q.kind === "fill" && (
                <div className="pl-7">
                  <textarea
                    value={(v as string) ?? ""}
                    onChange={(e) => setFill(q.id, e.target.value)}
                    placeholder={q.placeholder ?? ""}
                    rows={2}
                    disabled={submitted}
                    className="w-full px-2.5 py-2 text-[12px] border border-[var(--color-line)] rounded-md focus:border-[var(--color-accent)] outline-none resize-none"
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
        {submitted ? (
          <>
            <span
              className="inline-flex items-center gap-1 text-[11px] font-medium"
              style={{ color: "var(--color-green)" }}
            >
              <Check size={11} /> 已发送你的回复 · {payload.questions.length} 项
            </span>
            <button
              type="button"
              onClick={() => setSubmitted(false)}
              className="ml-auto px-2.5 py-1 text-[11px] rounded hover:bg-[var(--color-line)]"
            >
              修改回复
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="px-2.5 py-1 text-[11px] rounded text-[var(--color-fg-3)] hover:bg-[var(--color-line)]"
            >
              稍后
            </button>
            <button
              type="button"
              disabled={!allRequiredAnswered}
              onClick={() => setSubmitted(true)}
              className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 text-[11.5px] font-medium rounded bg-[var(--color-accent)] text-white disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Send size={11} /> 提交回复
            </button>
          </>
        )}
      </div>
    </div>
  );
}
