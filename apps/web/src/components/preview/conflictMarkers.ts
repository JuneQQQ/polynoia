/** Parse git conflict markers into structured segments for per-hunk resolution.
 *
 * probe_merge captures the working-tree file WITH diff3-style markers:
 *
 *   <<<<<<< HEAD
 *   <ours / main side — already-merged agents>
 *   ||||||| <base sha>            (only in diff3 style; may be absent)
 *   <merge base>
 *   =======
 *   <theirs / the conflicting branch>
 *   >>>>>>> <branch>
 *
 * A `content` conflict has UNCHANGED context lines around (possibly several)
 * conflict blocks. Resolving per-block — instead of taking one whole side —
 * lets us keep BOTH agents' independent edits and only ask about the lines
 * that truly collide. `assembleResolution` rebuilds the final file text from
 * the user's per-block choices; the unchanged context is preserved verbatim.
 */

export type ConflictSegment =
  | { type: "context"; lines: string[] }
  | { type: "conflict"; ours: string[]; base: string[]; theirs: string[] };

export type BlockChoice = "ours" | "theirs" | "both" | { edit: string };

const lead = (l: string, marker: string) => l.startsWith(marker);

export function parseConflictMarkers(markers: string): ConflictSegment[] {
  const lines = markers.split("\n");
  const segs: ConflictSegment[] = [];
  let ctx: string[] = [];
  const flushCtx = () => {
    if (ctx.length) {
      segs.push({ type: "context", lines: ctx });
      ctx = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (lead(line, "<<<<<<<")) {
      flushCtx();
      const ours: string[] = [];
      const base: string[] = [];
      const theirs: string[] = [];
      let bucket: "ours" | "base" | "theirs" = "ours";
      i++;
      while (i < lines.length && !lead(lines[i], ">>>>>>>")) {
        const l = lines[i];
        if (lead(l, "|||||||")) bucket = "base";
        else if (lead(l, "=======")) bucket = "theirs";
        else if (bucket === "ours") ours.push(l);
        else if (bucket === "base") base.push(l);
        else theirs.push(l);
        i++;
      }
      i++; // consume the >>>>>>> line
      segs.push({ type: "conflict", ours, base, theirs });
    } else {
      ctx.push(line);
      i++;
    }
  }
  flushCtx();
  return segs;
}

/** Count the conflict blocks (drives the "N 处冲突" counter). */
export function countConflictBlocks(segs: ConflictSegment[]): number {
  return segs.filter((s) => s.type === "conflict").length;
}

/** Rebuild the resolved file text from per-block choices. `choices` is indexed
 *  by conflict-block order (context segments are emitted verbatim). */
export function assembleResolution(segs: ConflictSegment[], choices: BlockChoice[]): string {
  const out: string[] = [];
  let bi = 0;
  for (const s of segs) {
    if (s.type === "context") {
      out.push(...s.lines);
    } else {
      const ch = choices[bi] ?? "theirs";
      bi++;
      if (ch === "ours") out.push(...s.ours);
      else if (ch === "theirs") out.push(...s.theirs);
      else if (ch === "both") out.push(...s.ours, ...s.theirs);
      else out.push(...ch.edit.split("\n"));
    }
  }
  return out.join("\n");
}
