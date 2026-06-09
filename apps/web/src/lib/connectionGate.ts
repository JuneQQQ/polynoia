/** Entry gate for the native mobile (Capacitor) shell.
 *
 * A phone has no local backend, so it MUST be pointed at a reachable Polynoia
 * server before the chat UI is allowed to render. The gate keys off a *verified
 * live connection* for this session — not merely whether a URL was once saved.
 * Otherwise a returning user whose saved server is now down would be dropped
 * straight into an empty chat UI (the bug this fixes).
 *
 * Returns:
 *  - "connect"    → show ConnectServerScreen (first run, or saved server down)
 *  - "connecting" → show the verifying splash (probe in flight this session)
 *  - "chat"       → connection verified; render the normal mobile layout
 *  - null         → not a native mobile shell (web / desktop / narrow browser);
 *                   the caller's own boot gate applies instead.
 */
export type MobileGate = "connect" | "connecting" | "chat" | null;

export function resolveMobileGate(s: {
	/** Mobile layout active (Capacitor native OR a narrow browser viewport). */
	mobile: boolean;
	/** The native mobile shell that must point at a remote backend — the bundled
	 * Capacitor app, OR a live-reload load flagged native (see isNativeShell). */
	nativeShell: boolean;
	/** A remote server URL has been configured/persisted. */
	hasOverride: boolean;
	/** The initial seed probe has resolved this session (success OR failure). */
	connectionProbed: boolean;
	/** The configured server answered the last probe. */
	serverReachable: boolean;
}): MobileGate {
	// Only the native shell is gated. Browser/desktop have a same-origin/local
	// default and keep their existing boot behaviour.
	if (!(s.mobile && s.nativeShell)) return null;
	// Never configured → first-run connect screen.
	if (!s.hasOverride) return "connect";
	// Configured but not yet verified this session → hold on the splash.
	if (!s.connectionProbed) return "connecting";
	// Verified unreachable → back to the connect screen (re-enter / retry).
	if (!s.serverReachable) return "connect";
	// Verified live → enter the app.
	return "chat";
}
