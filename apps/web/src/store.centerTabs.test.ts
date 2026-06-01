/** Center-tab reducers (Phase 2): open/close file tabs, terminal tab, reset.
 * These back CenterTabs.tsx — the center column's 「聊天 | file | terminal」strip. */
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";

const s = () => useStore.getState();

describe("center-tab reducers", () => {
  beforeEach(() => {
    useStore.setState({ centerFileTabs: [], activeCenterTab: "chat", terminalOpen: false });
  });

  it("openCenterFile adds a tab and makes it active", () => {
    s().openCenterFile("src/a.ts");
    expect(s().centerFileTabs).toEqual(["src/a.ts"]);
    expect(s().activeCenterTab).toBe("src/a.ts");
  });

  it("openCenterFile is idempotent (no duplicate) but re-activates", () => {
    s().openCenterFile("src/a.ts");
    s().openCenterFile("src/b.ts");
    s().openCenterFile("src/a.ts");
    expect(s().centerFileTabs).toEqual(["src/a.ts", "src/b.ts"]);
    expect(s().activeCenterTab).toBe("src/a.ts");
  });

  it("closeCenterFile removes the tab; active falls back to the last remaining", () => {
    s().openCenterFile("src/a.ts");
    s().openCenterFile("src/b.ts");
    s().closeCenterFile("src/b.ts");
    expect(s().centerFileTabs).toEqual(["src/a.ts"]);
    expect(s().activeCenterTab).toBe("src/a.ts");
  });

  it("closeCenterFile on a non-active tab keeps the active one", () => {
    s().openCenterFile("src/a.ts");
    s().openCenterFile("src/b.ts");
    s().setActiveCenterTab("chat");
    s().closeCenterFile("src/a.ts");
    expect(s().centerFileTabs).toEqual(["src/b.ts"]);
    expect(s().activeCenterTab).toBe("chat");
  });

  it("closing the last tab falls back to chat", () => {
    s().openCenterFile("src/a.ts");
    s().closeCenterFile("src/a.ts");
    expect(s().centerFileTabs).toEqual([]);
    expect(s().activeCenterTab).toBe("chat");
  });

  it("reorderCenterFile moves a tab to another's slot", () => {
    s().openCenterFile("a.ts");
    s().openCenterFile("b.ts");
    s().openCenterFile("c.ts");
    // drag c before a
    s().reorderCenterFile("c.ts", "a.ts");
    expect(s().centerFileTabs).toEqual(["c.ts", "a.ts", "b.ts"]);
    // drag a to the end (onto b)
    s().reorderCenterFile("a.ts", "b.ts");
    expect(s().centerFileTabs).toEqual(["c.ts", "b.ts", "a.ts"]);
    // no-op on self / unknown
    s().reorderCenterFile("c.ts", "c.ts");
    s().reorderCenterFile("zzz.ts", "a.ts");
    expect(s().centerFileTabs).toEqual(["c.ts", "b.ts", "a.ts"]);
  });

  it("toggleTerminal flips the docked terminal panel", () => {
    expect(s().terminalOpen).toBe(false);
    s().toggleTerminal();
    expect(s().terminalOpen).toBe(true);
    s().toggleTerminal();
    expect(s().terminalOpen).toBe(false);
  });

  it("resetCenterTabs clears tabs + closes the terminal", () => {
    s().openCenterFile("src/a.ts");
    s().toggleTerminal();
    s().resetCenterTabs();
    expect(s().centerFileTabs).toEqual([]);
    expect(s().activeCenterTab).toBe("chat");
    expect(s().terminalOpen).toBe(false);
  });
});

describe("review-cursor (Phase 4)", () => {
  beforeEach(() => {
    useStore.setState({ reviewIndex: 0 });
  });

  it("setReviewIndex never goes below 0", () => {
    s().setReviewIndex(3);
    expect(s().reviewIndex).toBe(3);
    s().setReviewIndex(-5);
    expect(s().reviewIndex).toBe(0);
  });

  it("resetCenterTabs also resets the review cursor", () => {
    s().setReviewIndex(4);
    s().resetCenterTabs();
    expect(s().reviewIndex).toBe(0);
  });
});
