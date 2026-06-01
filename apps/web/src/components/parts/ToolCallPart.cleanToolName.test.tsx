import { describe, expect, it } from "vitest";
import { cleanToolName } from "./ToolCallPart";

// Every Code-agent adapter must surface the SAME bare verb — no server prefix.
describe("cleanToolName", () => {
  it("strips Claude Code mcp__<server>__ prefix", () => {
    expect(cleanToolName("mcp__polynoia__write")).toBe("write");
    expect(cleanToolName("mcp__polynoia__apply_patch")).toBe("apply_patch");
  });

  it("strips Codex <server>:: prefix", () => {
    expect(cleanToolName("polynoia::write")).toBe("write");
    expect(cleanToolName("polynoia::recall")).toBe("recall");
    expect(cleanToolName("polynoia::apply_patch")).toBe("apply_patch");
  });

  it("strips OpenCode polynoia_ prefix", () => {
    expect(cleanToolName("polynoia_read")).toBe("read");
  });

  it("leaves bare verbs untouched", () => {
    expect(cleanToolName("read")).toBe("read");
    expect(cleanToolName("edit")).toBe("edit");
  });
});
