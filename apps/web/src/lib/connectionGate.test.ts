import { describe, expect, it } from "vitest";
import { resolveMobileGate } from "./connectionGate";

describe("resolveMobileGate", () => {
	// Web / desktop / narrow-browser are not governed by the native connect gate.
	it("returns null when not mobile", () => {
		expect(
			resolveMobileGate({
				mobile: false,
				nativeShell: false,
				hasOverride: false,
				connectionProbed: false,
				serverReachable: true,
			}),
		).toBeNull();
	});

	it("returns null on a narrow browser (mobile layout but not a native shell)", () => {
		expect(
			resolveMobileGate({
				mobile: true,
				nativeShell: false,
				hasOverride: false,
				connectionProbed: true,
				serverReachable: true,
			}),
		).toBeNull();
	});

	it("shows the connect screen on a genuine first run (no server configured)", () => {
		expect(
			resolveMobileGate({
				mobile: true,
				nativeShell: true,
				hasOverride: false,
				connectionProbed: false,
				serverReachable: true,
			}),
		).toBe("connect");
	});

	it("shows the connecting splash while the saved server is still being verified", () => {
		expect(
			resolveMobileGate({
				mobile: true,
				nativeShell: true,
				hasOverride: true,
				connectionProbed: false,
				serverReachable: true, // optimistic default — not yet confirmed
			}),
		).toBe("connecting");
	});

	// The bug: a returning user whose saved server is unreachable must NOT be
	// dropped into the chat UI — they must land back on the connect screen.
	it("shows the connect screen when the saved server was probed and is unreachable", () => {
		expect(
			resolveMobileGate({
				mobile: true,
				nativeShell: true,
				hasOverride: true,
				connectionProbed: true,
				serverReachable: false,
			}),
		).toBe("connect");
	});

	it("enters chat only once a live connection is verified", () => {
		expect(
			resolveMobileGate({
				mobile: true,
				nativeShell: true,
				hasOverride: true,
				connectionProbed: true,
				serverReachable: true,
			}),
		).toBe("chat");
	});
});
