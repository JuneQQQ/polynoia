/** Parse a server timestamp into a Date, treating tz-less ISO strings as UTC.
 *
 * The backend is inconsistent about timezone markers: message rows serialize
 * `created_at` with a trailing "Z" (UTC), but Pydantic models — notably
 * `Conversation.created_at` / `last_message_at` / `updated_at` — serialize a
 * NAIVE `datetime.utcnow()` WITHOUT any tz designator (e.g.
 * "2026-06-19T13:44:49.631639"). `new Date("…no tz…")` is parsed as LOCAL time,
 * so a UTC value renders 8 hours off in +08:00 (the「消息时间和时区不对应」bug).
 *
 * Normalize defensively here: if the string carries no timezone (no trailing Z
 * and no ±HH:MM offset), append "Z" so it's interpreted as UTC. Values that
 * already include a tz are left untouched. Returns null for empty/invalid input
 * so callers can fall back gracefully. */
export function parseServerTime(iso: string | null | undefined): Date | null {
	if (!iso) return null;
	const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
	const d = new Date(hasTz ? iso : `${iso}Z`);
	return Number.isNaN(d.getTime()) ? null : d;
}
