/** PendingEditsPanel — manual-mode approval cards floating above Composer.
 *
 * Visual refresh per docs/diagrams/chat-ui-redesign.md Image 2:
 *   - Left 4px orange accent stripe(was 2px),drawing eye immediately
 *   - Big mono-CAPS ACCEPT(green)/ REJECT(red)buttons, not pills
 *   - Amber countdown "TIMES OUT IN X:XX" on right
 *   - QUEUED · N section for the 2nd+ cards(dimmed,not interactable
 *     until the first is decided — prevents accidental rapid-fire)
 *
 * Pulls from store.pendingEditsByConv[convId]. Hydrated on conv switch
 * via api.listPendingEdits + real-time via the `data-pending-edit` WS
 * chunk handler in ChatPane.
 */
import { Check, FileEdit, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type PendingEdit } from "../lib/api";
import { useStore } from "../store";

type Props = { convId: string };

// Stable empty-array fallback (avoids Zustand re-render loop).
const EMPTY_LIST: readonly PendingEdit[] = [];

// Per-edit timeout the server enforces (5 minutes per ADR-009).
const TIMEOUT_SECONDS = 5 * 60;

export function PendingEditsPanel({ convId }: Props) {
  const list = useStore((s) => s.pendingEditsByConv.get(convId) ?? EMPTY_LIST);
  const hydrate = useStore((s) => s.hydratePendingEdits);
  const upsert = useStore((s) => s.upsertPendingEdit);
  const agents = useStore((s) => s.agents);

  useEffect(() => {
    let alive = true;
    api.listPendingEdits(convId, "pending")
      .then((edits) => alive && hydrate(convId, edits))
      .catch(() => {});
    return () => { alive = false; };
  }, [convId, hydrate]);

  const pending = list.filter((e) => e.status === "pending");
  if (pending.length === 0) return null;

  const [active, ...queued] = pending;

  return (
    <div className="px-6 pb-3 pt-3 space-y-2 border-t border-[var(--color-line)] bg-[var(--color-accent-soft)]/20">
      <div className="flex items-center gap-2 text-[10.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-accent)] font-medium">
        <Loader2 size={11} className="animate-spin" />
        <span>Awaiting approval · {pending.length}</span>
      </div>
      <PendingEditCard
        edit={active}
        onUpdate={upsert}
        agents={agents}
        active
      />
      {queued.length > 0 && (
        <>
          <div className="flex items-center gap-2 mt-3 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)]">
            <span className="h-px flex-1 bg-[var(--color-line)]" />
            <span>Queued · {queued.length}</span>
            <span className="h-px flex-1 bg-[var(--color-line)]" />
          </div>
          {queued.map((edit) => (
            <PendingEditCard
              key={edit.id}
              edit={edit}
              onUpdate={upsert}
              agents={agents}
              active={false}
            />
          ))}
        </>
      )}
    </div>
  );
}

function PendingEditCard({
  edit,
  onUpdate,
  agents,
  active,
}: {
  edit: PendingEdit;
  onUpdate: (e: PendingEdit) => void;
  agents: { id: string; name: string; color: string; initials: string }[];
  /** Only the head-of-queue card is interactable; queued ones render
   * dimmed (50% opacity) so the user processes one at a time. */
  active: boolean;
}) {
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);

  const agent = agents.find((a) => a.id === edit.agent_id);

  // Countdown timer — derived from edit.created_at, refreshed every second.
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, [active]);

  const remainingSec = (() => {
    if (!edit.created_at) return TIMEOUT_SECONDS;
    const createdMs = new Date(edit.created_at).getTime();
    const elapsed = Math.floor((now - createdMs) / 1000);
    return Math.max(0, TIMEOUT_SECONDS - elapsed);
  })();
  const cdMin = Math.floor(remainingSec / 60);
  const cdSec = remainingSec % 60;

  const decide = async (decision: "accept" | "reject") => {
    if (busy || !active) return;
    setBusy(decision);
    try {
      const updated = decision === "accept"
        ? await api.approvePendingEdit(edit.id)
        : await api.rejectPendingEdit(edit.id);
      onUpdate(updated);
    } catch (e) {
      console.error("decide failed", e);
    } finally {
      setBusy(null);
    }
  };

  const preview = (() => {
    const a = edit.args as Record<string, unknown>;
    if (edit.kind === "edit") {
      return {
        kind: "edit" as const,
        old: String(a.old_string ?? "").slice(0, 200),
        neu: String(a.new_string ?? "").slice(0, 200),
      };
    }
    if (edit.kind === "write") {
      return { kind: "write" as const, content: String(a.content ?? "").slice(0, 300) };
    }
    return { kind: "patch" as const, patch: String(a.patch_text ?? "").slice(0, 300) };
  })();

  return (
    <div
      className={`relative bg-[var(--color-surface)] rounded-md overflow-hidden border border-[var(--color-line)] ${
        active ? "" : "opacity-50"
      }`}
    >
      {/* 4px left accent stripe — main visual cue per design Image 2 */}
      <span
        aria-hidden
        className="absolute left-0 top-0 bottom-0 w-[4px]"
        style={{ background: "var(--color-accent)" }}
      />
      {/* Header row */}
      <div className="flex items-center gap-2 pl-4 pr-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <FileEdit size={12} className="text-[var(--color-accent)]" />
        <span className="font-mono text-[11.5px] text-[var(--color-fg)] truncate flex-1">
          {edit.file_path || "(multi-file patch)"}
        </span>
        <span className="text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--color-fg-3)] font-medium">
          {edit.kind}
        </span>
        {agent && (
          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-[var(--color-fg-2)]">
            <span
              className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
              style={{ background: agent.color }}
            >
              {agent.initials}
            </span>
            <span>{agent.name}</span>
          </span>
        )}
      </div>
      {/* Body — diff preview */}
      <div className="pl-4 pr-3 py-2.5 text-[12px] font-mono leading-relaxed max-h-[160px] overflow-y-auto">
        {preview.kind === "edit" && (
          <>
            <div className="flex items-start gap-2 mb-1">
              <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[var(--color-red)] mt-1 flex-shrink-0">
                Removed
              </span>
              <div className="flex-1 bg-[var(--color-red-soft)] text-[var(--color-red)] px-2 py-1 rounded-sm whitespace-pre-wrap break-all">
                {preview.old}
              </div>
            </div>
            <div className="flex items-start gap-2">
              <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[var(--color-green)] mt-1 flex-shrink-0">
                Added
              </span>
              <div className="flex-1 bg-[var(--color-green-soft)] text-[var(--color-green)] px-2 py-1 rounded-sm whitespace-pre-wrap break-all">
                {preview.neu}
              </div>
            </div>
          </>
        )}
        {preview.kind === "write" && (
          <div className="whitespace-pre-wrap text-[var(--color-fg-2)] break-all">
            {preview.content}
          </div>
        )}
        {preview.kind === "patch" && (
          <div className="whitespace-pre-wrap text-[var(--color-fg-3)] break-all">
            {preview.patch}
          </div>
        )}
      </div>
      {/* Action row — large CAPS buttons + amber countdown */}
      <div className="flex items-center gap-2 pl-4 pr-3 py-2.5 bg-[var(--color-surface-2)] border-t border-[var(--color-line)]">
        <button
          type="button"
          onClick={() => decide("accept")}
          disabled={!!busy || !active}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-green)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
          title="Accept (Y)"
        >
          {busy === "accept" ? <Loader2 size={11} className="animate-spin" /> : <Check size={12} />}
          Accept
        </button>
        <button
          type="button"
          onClick={() => decide("reject")}
          disabled={!!busy || !active}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-red)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
          title="Reject (N)"
        >
          {busy === "reject" ? <Loader2 size={11} className="animate-spin" /> : <X size={12} />}
          Reject
        </button>
        {active && (
          <span className="ml-auto text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-amber)]">
            Times out in {cdMin}:{String(cdSec).padStart(2, "0")}
          </span>
        )}
      </div>
    </div>
  );
}
