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

/** Map a preset + its full markdown body onto the contact fields the create form
 * prefills — parity with the backend hire mapping minus governance (no
 * tool_role). system_prompt is the FULL body, like RolePresetLibrary's hire.
 * tagline is sourced from preset.description. */
export function rolePresetToContactFields(
	preset: RolePresetRow,
	body: string,
): { name: string; systemPrompt: string; color: string; tagline: string } {
	return {
		name: preset.name,
		systemPrompt: body,
		color: preset.color,
		tagline: preset.description,
	};
}
