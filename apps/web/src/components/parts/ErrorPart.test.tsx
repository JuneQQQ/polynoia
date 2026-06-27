import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ErrorPayload } from "../../lib/types";
import { ErrorPart } from "./ErrorPart";

const make = (over: Partial<ErrorPayload> = {}): ErrorPayload => ({
	kind: "error",
	message: "boom",
	reason: "exception",
	...over,
});

describe("ErrorPart", () => {
	it("renders the failure message", () => {
		expect(
			renderToStaticMarkup(
				<ErrorPart payload={make({ message: "401 unauthorized" })} />,
			),
		).toContain("401 unauthorized");
	});

	it("hard reasons (exception/turn_failed/timeout/unavailable) read as red", () => {
		for (const reason of [
			"exception",
			"turn_failed",
			"timeout",
			"unavailable",
		] as const) {
			const html = renderToStaticMarkup(
				<ErrorPart payload={make({ reason })} />,
			);
			expect(html).toContain("--color-red");
		}
	});

	it("aborted/depth_limit/queued are neutral, not red", () => {
		for (const reason of ["aborted", "depth_limit", "queued"] as const) {
			const html = renderToStaticMarkup(
				<ErrorPart payload={make({ reason })} />,
			);
			expect(html).not.toContain("--color-red");
		}
		expect(
			renderToStaticMarkup(<ErrorPart payload={make({ reason: "aborted" })} />),
		).toContain("已中断");
		expect(
			renderToStaticMarkup(<ErrorPart payload={make({ reason: "queued" })} />),
		).toContain("排队中");
	});

	it("shows a retry hint only when retryable", () => {
		expect(
			renderToStaticMarkup(<ErrorPart payload={make({ retryable: true })} />),
		).toContain("可重试");
		expect(
			renderToStaticMarkup(<ErrorPart payload={make({ retryable: false })} />),
		).not.toContain("可重试");
	});

	it("falls back to the exception preset for an unknown reason", () => {
		// reason missing → defaults to exception (hard/red), never crashes.
		const html = renderToStaticMarkup(
			<ErrorPart payload={{ kind: "error", message: "x" }} />,
		);
		expect(html).toContain("--color-red");
		expect(html).toContain("出错");
	});
});
