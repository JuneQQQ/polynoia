import type { TypingPayload } from "../../lib/types";

export function TypingPart({ payload }: { payload: TypingPayload }) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--color-surface-2)] border border-[var(--color-line)] text-[12px] text-[var(--color-fg-3)]">
      <span className="typing-dots inline-flex gap-0.5">
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </span>
      <span>{payload.note ?? "正在思考…"}</span>
      <style>{`
        .typing-dot {
          width: 4px; height: 4px; border-radius: 50%;
          background: var(--color-fg-3);
          animation: typing-bounce 1.2s infinite ease-in-out both;
        }
        .typing-dot:nth-child(1) { animation-delay: -0.32s; }
        .typing-dot:nth-child(2) { animation-delay: -0.16s; }
        @keyframes typing-bounce {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
