/** TerminalTab — an interactive shell (xterm.js) bound to the workspace's
 * main working directory, bridged to a real PTY over WebSocket (Phase 3).
 *
 * Docks in the bottom half of the explorer pane (PreviewPane). Keystrokes are sent
 * as binary frames (raw pty input); a resize control message (text/JSON) keeps
 * the pty's winsize in sync with the visible area so vim/top/git-pager render
 * correctly. See apps/server/polynoia/api/terminal.py for the server side.
 */
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef } from "react";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";

// Dark theme tuned to the app surface tokens (xterm needs concrete hex, not
// CSS vars — it paints to a canvas).
const THEME = {
	background: "#0e1116",
	foreground: "#d6dae0",
	cursor: "#5b8ff9",
	selectionBackground: "#2a3344",
	black: "#1f2430",
	red: "#e06c75",
	green: "#27ae60",
	yellow: "#e5c07b",
	blue: "#5b8ff9",
	magenta: "#c678dd",
	cyan: "#56b6c2",
	white: "#d6dae0",
	brightBlack: "#5c6370",
};

export function TerminalTab({ workspaceId }: { workspaceId: string }) {
	const hostRef = useRef<HTMLDivElement | null>(null);
	const lang = useStore((s) => s.lang);

	// biome-ignore lint/correctness/useExhaustiveDependencies: lang is only read in the ws.onclose [disconnected] notice; re-running this effect on a language switch would needlessly tear down and recreate the terminal + WebSocket.
	useEffect(() => {
		const host = hostRef.current;
		if (!host) return;
		let disposed = false;

		const term = new Terminal({
			fontSize: 12.5,
			fontFamily:
				'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace',
			theme: THEME,
			cursorBlink: true,
			scrollback: 5000,
		});
		const fit = new FitAddon();
		term.loadAddon(fit);
		term.open(host);

		const proto = window.location.protocol === "https:" ? "wss" : "ws";
		const ws = new WebSocket(
			`${proto}://${window.location.host}/ws/workspaces/${workspaceId}/terminal`,
		);
		ws.binaryType = "arraybuffer";
		const enc = new TextEncoder();

		const sendResize = () => {
			if (ws.readyState !== WebSocket.OPEN) return;
			ws.send(
				JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }),
			);
		};

		const doFit = () => {
			if (disposed) return;
			try {
				fit.fit();
			} catch {
				/* container not laid out yet */
			}
			sendResize();
		};

		ws.onopen = () => {
			doFit();
			term.focus();
		};
		ws.onmessage = (ev) => {
			if (ev.data instanceof ArrayBuffer) term.write(new Uint8Array(ev.data));
			else if (typeof ev.data === "string") term.write(ev.data);
		};
		ws.onclose = () => {
			if (!disposed)
				term.write(`\r\n\x1b[2m${t("disconnected", lang)}\x1b[0m\r\n`);
		};

		// Keystrokes → binary pty input.
		const dataSub = term.onData((d) => {
			if (ws.readyState === WebSocket.OPEN) ws.send(enc.encode(d));
		});

		// Keep the pty sized to the visible area.
		const ro = new ResizeObserver(() => doFit());
		ro.observe(host);
		// First layout pass may not be ready synchronously after open().
		const raf = requestAnimationFrame(doFit);

		return () => {
			disposed = true;
			cancelAnimationFrame(raf);
			ro.disconnect();
			dataSub.dispose();
			try {
				ws.close();
			} catch {
				/* already closing */
			}
			term.dispose();
		};
	}, [workspaceId]);

	return (
		<div className="flex flex-col h-full bg-[#0e1116]">
			<div className="px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] text-[var(--color-fg-3)] mono">
				{t("terminalMainDir", lang)}
			</div>
			<div ref={hostRef} className="flex-1 min-h-0 overflow-hidden p-1.5" />
		</div>
	);
}
