import { describe, expect, it } from "vitest";
import { lineDiffUnified } from "./diffUnified";

/** Pull just the diff body (lines after the first @@ header) for assertions. */
function body(unified: string): string[] {
  const lines = unified.split("\n");
  const at = lines.findIndex((l) => l.startsWith("@@"));
  return lines.slice(at + 1);
}

/** Count `@@ … @@` hunk headers — i.e. how many separate regions are shown. */
function hunkCount(unified: string): number {
  return unified.split("\n").filter((l) => l.startsWith("@@")).length;
}

describe("lineDiffUnified — default (single whole-file hunk)", () => {
  it("identical content → all context, no +/-", () => {
    const r = lineDiffUnified("a\nb\nc\n", "a\nb\nc\n", "f.txt");
    expect(r.adds).toBe(0);
    expect(r.dels).toBe(0);
    expect(r.blocks).toBe(0);
    expect(body(r.unified)).toEqual([" a", " b", " c"]);
  });

  it("single mid-line replace keeps surrounding lines as context", () => {
    const r = lineDiffUnified("a\nb\nc\n", "a\nB\nc\n", "f.txt");
    expect(r.dels).toBe(1);
    expect(r.adds).toBe(1);
    expect(r.blocks).toBe(1);
    expect(body(r.unified)).toEqual([" a", "-b", "+B", " c"]);
  });

  it("pure insertion in the middle → 1 add, rest context", () => {
    const r = lineDiffUnified("a\nc\n", "a\nb\nc\n", "f.txt");
    expect(r.dels).toBe(0);
    expect(r.adds).toBe(1);
    expect(body(r.unified)).toEqual([" a", "+b", " c"]);
  });

  it("pure deletion in the middle → 1 del, rest context", () => {
    const r = lineDiffUnified("a\nb\nc\n", "a\nc\n", "f.txt");
    expect(r.dels).toBe(1);
    expect(r.adds).toBe(0);
    expect(body(r.unified)).toEqual([" a", "-b", " c"]);
  });

  it("new file (empty old) → all additions, -0,0 header", () => {
    const r = lineDiffUnified("", "x\ny\n", "f.txt");
    expect(r.dels).toBe(0);
    expect(r.adds).toBe(2);
    expect(r.unified).toContain("@@ -0,0 +1,2 @@");
    expect(body(r.unified)).toEqual(["+x", "+y"]);
  });

  it("deleted file (empty new) → all deletions, +0,0 header", () => {
    const r = lineDiffUnified("x\ny\n", "", "f.txt");
    expect(r.dels).toBe(2);
    expect(r.adds).toBe(0);
    expect(r.unified).toContain("@@ -1,2 +0,0 @@");
    expect(body(r.unified)).toEqual(["-x", "-y"]);
  });

  it("add_add version strings → exactly 1 del + 1 add", () => {
    const r = lineDiffUnified("v1.0.0\n", "release-2026\n", "VERSION.txt");
    expect(r.dels).toBe(1);
    expect(r.adds).toBe(1);
    expect(body(r.unified)).toEqual(["-v1.0.0", "+release-2026"]);
  });

  it("groups deletes before inserts within a change run (clean split pairing)", () => {
    const r = lineDiffUnified("a\nb\nc\nd\n", "a\nX\nY\nd\n", "f.txt");
    expect(body(r.unified)).toEqual([" a", "-b", "-c", "+X", "+Y", " d"]);
  });
});

describe("lineDiffUnified — blocks counter", () => {
  it("two separated changes → blocks = 2", () => {
    const r = lineDiffUnified("a\nb\nc\nd\ne\n", "A\nb\nc\nd\nE\n", "f.txt");
    expect(r.blocks).toBe(2);
  });

  it("one contiguous change run → blocks = 1", () => {
    const r = lineDiffUnified("a\nb\nc\nd\n", "a\nX\nY\nd\n", "f.txt");
    expect(r.blocks).toBe(1);
  });
});

describe("lineDiffUnified — context folding (long-file conflict locating)", () => {
  const ten = "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n";

  it("folds far-away unchanged lines, keeping only ±context around the change", () => {
    const changed = "1\n2\n3\n4\nX\n6\n7\n8\n9\n10\n"; // line 5: 5 → X
    const r = lineDiffUnified(ten, changed, "f.txt", { context: 1 });
    expect(r.blocks).toBe(1);
    expect(hunkCount(r.unified)).toBe(1);
    // Only line 4 (context), -5, +X, line 6 (context) — distant lines folded away.
    expect(body(r.unified)).toEqual([" 4", "-5", "+X", " 6"]);
    expect(r.unified).toContain("@@ -4,3 +4,3 @@");
    expect(r.unified).not.toContain(" 1");
    expect(r.unified).not.toContain(" 9");
  });

  it("two far-apart changes → two separate hunks", () => {
    const changed = "1\nB\n3\n4\n5\n6\n7\n8\nI\n10\n"; // line 2 and line 9
    const r = lineDiffUnified(ten, changed, "f.txt", { context: 1 });
    expect(r.blocks).toBe(2);
    expect(hunkCount(r.unified)).toBe(2);
  });

  it("two nearby changes merge into one hunk when within 2·context", () => {
    const changed = "1\n2\n3\nD\n5\nF\n7\n8\n9\n10\n"; // line 4 and line 6 (gap = 1)
    const r = lineDiffUnified(ten, changed, "f.txt", { context: 3 });
    expect(r.blocks).toBe(2);
    expect(hunkCount(r.unified)).toBe(1);
  });
});
