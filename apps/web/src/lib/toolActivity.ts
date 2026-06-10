export function isLiveToolState(state: unknown): boolean {
	return state === "pending" || state === "running" || state === "run";
}

export function isActiveToolMember(payload: unknown): boolean {
	const p = payload as
		| { kind?: unknown; running?: unknown; state?: unknown }
		| null
		| undefined;
	if (!p || typeof p !== "object") return false;
	if (p.kind === "terminal") return p.running === true;
	if (p.kind === "tool-call") return isLiveToolState(p.state);
	return false;
}
