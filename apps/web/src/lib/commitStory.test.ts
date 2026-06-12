/** commitStory — timeline folding / provenance parsing / day grouping. */
import { describe, expect, it } from "vitest";
import type { CommitMeta } from "./api";
import {
	buildTimeline,
	firstParentChain,
	groupByDay,
	parseAgentFromText,
	parseConvFromText,
	stripStatSuffix,
} from "./commitStory";

const C = (
	sha: string,
	author: string,
	parents: string[],
	over: Partial<CommitMeta> = {},
): CommitMeta => ({
	sha,
	short: sha.slice(0, 7),
	author,
	email: "a@b",
	date: "2026-06-11T10:00:00",
	subject: `edit ${sha} (+1/-0)`,
	files: 1,
	additions: 1,
	deletions: 0,
	parents,
	...over,
});

describe("stripStatSuffix", () => {
	it("strips the machine stats suffix", () => {
		expect(stripStatSuffix("edit dash.css (+38/-0)")).toBe("edit dash.css");
		expect(stripStatSuffix("edit a.md (+24/-6)")).toBe("edit a.md");
	});
	it("leaves human subjects alone", () => {
		expect(stripStatSuffix("修复登录问题")).toBe("修复登录问题");
		expect(stripStatSuffix("release (final)")).toBe("release (final)");
	});
});

describe("provenance parsing", () => {
	const ULID_A = "01KTXFF6T5QJ3BBYWMV9J3ZDAJ";
	const ULID_C = "01KTXFF73TWADK386WHGV0HREV";
	it("extracts conv + agent from the canonical branch ref", () => {
		const s = `polynoia: merge agent/${ULID_A}/conv-${ULID_C} into main`;
		expect(parseConvFromText(s)).toBe(ULID_C);
		expect(parseAgentFromText(s)).toBe(ULID_A);
	});
	it("returns null when absent", () => {
		expect(parseConvFromText("polynoia: user edit a.md")).toBeNull();
		expect(parseAgentFromText(null)).toBeNull();
	});
});

describe("buildTimeline", () => {
	// History (newest first), single-author branch b2-b1 merged by M:
	//   M(main merge) → parents [m1, b2]
	//   b2 → b1 → m1 ;  m1 → root
	const history = [
		C("M", "polynoia-agent", ["m1", "b2"], { is_merge: true }),
		C("b2", "agentZ", ["b1"]),
		C("m1", "agentS", ["root"]),
		C("b1", "agentZ", ["root"]),
		C("root", "you", []),
	];

	it("folds a single-author branch + merge into one round", () => {
		const tl = buildTimeline(history);
		const round = tl.find((t) => t.kind === "round");
		expect(round).toBeTruthy();
		if (round?.kind !== "round") throw new Error("unreachable");
		expect(round.author).toBe("agentZ");
		expect(round.commits.map((c) => c.sha)).toEqual(["b2", "b1"]);
		// branch commits no longer appear top-level
		const topShas = tl
			.filter(
				(t): t is Extract<typeof t, { kind: "commit" }> => t.kind === "commit",
			)
			.map((t) => t.commit.sha);
		expect(topShas).toEqual(["m1", "root"]);
	});

	it("keeps a mixed-author branch unfolded (merge separator + inline rows)", () => {
		const mixed = [
			C("M", "polynoia-agent", ["m1", "b2"], { is_merge: true }),
			C("b2", "agentZ", ["b1"]),
			C("m1", "agentS", ["root"]),
			C("b1", "agentW", ["root"]), // different author
			C("root", "you", []),
		];
		const tl = buildTimeline(mixed);
		expect(tl.some((t) => t.kind === "round")).toBe(false);
		expect(tl.filter((t) => t.kind === "merge")).toHaveLength(1);
		expect(tl.filter((t) => t.kind === "commit")).toHaveLength(4);
	});

	it("does not fold when the branch root predates the window", () => {
		const windowed = [
			C("M", "polynoia-agent", ["m1", "b2"], { is_merge: true }),
			C("b2", "agentZ", ["outside"]), // parent not loaded
			C("m1", "agentS", ["root"]),
			C("root", "you", []),
		];
		const tl = buildTimeline(windowed);
		expect(tl.some((t) => t.kind === "round")).toBe(false);
	});

	it("firstParentChain follows only first parents", () => {
		const chain = firstParentChain(history);
		expect(chain.has("M")).toBe(true);
		expect(chain.has("m1")).toBe(true);
		expect(chain.has("root")).toBe(true);
		expect(chain.has("b1")).toBe(false);
	});
});

describe("groupByDay", () => {
	it("merges repeated day keys into one group", () => {
		const items = [
			{ d: "2026-06-12T09:00:00" },
			{ d: "2026-06-11T10:00:00" },
			{ d: "2026-06-12T20:00:00" }, // same day as #1 but later in list
		];
		const groups = groupByDay(items, (i) => i.d);
		expect(groups).toHaveLength(2);
		expect(groups[0][1]).toHaveLength(2);
	});
});
