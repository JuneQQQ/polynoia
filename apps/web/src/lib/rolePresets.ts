/** Pure helpers for the agency-agents role-preset picker. React-free on purpose
 * so they unit-test in isolation (the component layer is in
 * components/RolePresetPicker.tsx). */

/** A role-preset list row, as returned by api.rolePresets().presets[]. */
export type RolePresetRow = {
	id: string;
	name: string;
	division_label: string;
	description: string;
	color: string;
};

/** Case-insensitive substring filter over name / division label / description. */
export function filterRolePresets(
	presets: RolePresetRow[],
	query: string,
): RolePresetRow[] {
	const q = query.trim().toLowerCase();
	if (!q) return presets;
	return presets.filter(
		(p) =>
			p.name.toLowerCase().includes(q) ||
			p.division_label.toLowerCase().includes(q) ||
			p.description.toLowerCase().includes(q),
	);
}
