import { describe, expect, it } from "vitest";
import { isActiveToolMember, isLiveToolState } from "./toolActivity";

describe("tool activity state", () => {
	it("treats pending/running/run as live tool states", () => {
		expect(isLiveToolState("pending")).toBe(true);
		expect(isLiveToolState("running")).toBe(true);
		expect(isLiveToolState("run")).toBe(true);
	});

	it("does not treat terminal tool states as live", () => {
		expect(isLiveToolState("completed")).toBe(false);
		expect(isLiveToolState("error")).toBe(false);
		expect(isLiveToolState("done")).toBe(false);
		expect(isLiveToolState(undefined)).toBe(false);
	});

	it("detects active terminal and tool-call members", () => {
		expect(isActiveToolMember({ kind: "terminal", running: true })).toBe(true);
		expect(isActiveToolMember({ kind: "terminal", running: false })).toBe(
			false,
		);
		expect(isActiveToolMember({ kind: "tool-call", state: "pending" })).toBe(
			true,
		);
		expect(isActiveToolMember({ kind: "tool-call", state: "completed" })).toBe(
			false,
		);
		expect(isActiveToolMember({ kind: "reasoning" })).toBe(false);
	});
});
