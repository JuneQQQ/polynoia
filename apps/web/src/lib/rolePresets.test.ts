import { describe, expect, it } from "vitest";
import {
	type RolePresetRow,
	filterRolePresets,
	rolePresetToContactFields,
} from "./rolePresets";

const rows: RolePresetRow[] = [
	{
		id: "1",
		name: "Backend Architect",
		division_label: "工程",
		description: "Scalable APIs and services",
		color: "#3D7FD1",
	},
	{
		id: "2",
		name: "UI Designer",
		division_label: "设计",
		description: "Pixel-perfect interfaces",
		color: "#7A5AE0",
	},
];

describe("filterRolePresets", () => {
	it("returns all rows when the query is empty or whitespace", () => {
		expect(filterRolePresets(rows, "")).toHaveLength(2);
		expect(filterRolePresets(rows, "   ")).toHaveLength(2);
	});
	it("matches on name, case-insensitively", () => {
		expect(filterRolePresets(rows, "BACKEND").map((r) => r.id)).toEqual(["1"]);
	});
	it("matches on division label", () => {
		expect(filterRolePresets(rows, "设计").map((r) => r.id)).toEqual(["2"]);
	});
	it("matches on description", () => {
		expect(filterRolePresets(rows, "interfaces").map((r) => r.id)).toEqual([
			"2",
		]);
	});
	it("returns [] when nothing matches", () => {
		expect(filterRolePresets(rows, "zzz")).toEqual([]);
	});
});

describe("rolePresetToContactFields", () => {
	it("takes name+color from the row and system_prompt from the full body (hire parity)", () => {
		expect(rolePresetToContactFields(rows[0], "FULL ROLE BODY")).toEqual({
			name: "Backend Architect",
			systemPrompt: "FULL ROLE BODY",
			color: "#3D7FD1",
		});
	});
});
