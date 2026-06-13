/** ThemeToggle — theme PICKER (was a binary dark⇄light flip).
 *
 * Theme is a pure presentation concern: it lives in the DOM
 * (`<html data-theme>`) + localStorage, NOT in the Zustand store. The
 * pre-paint restore script in index.html applies the saved preset before
 * first paint (no FOUC). This component just lists the presets and flips +
 * persists the attribute. No business logic touched.
 */
import { Check, Palette } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { type TKey, t } from "../lib/i18n";
import { applyStatusBarTheme } from "../lib/native";
import { useStore } from "../store";

const KEY = "polynoia-theme";

/** Preset id → { nameKey, swatch }. swatch = [bg, accent] for the chip. The
 * display name is resolved via the i18n dictionary at render time. Must stay in
 * sync with the `[data-theme="…"]` blocks in index.css and the allow-list in
 * index.html's restore script. */
const THEMES: { id: string; nameKey: TKey; bg: string; accent: string }[] = [
	{ id: "dark", nameKey: "themeDark", bg: "#14110c", accent: "#ec8a4c" },
	{ id: "light", nameKey: "themeLight", bg: "#f6f2ea", accent: "#e07a3c" },
	{ id: "nord", nameKey: "themeNord", bg: "#2e3440", accent: "#88c0d0" },
	{ id: "forest", nameKey: "themeForest", bg: "#2d353b", accent: "#a7c080" },
	{ id: "dusk", nameKey: "themeDusk", bg: "#1a1b26", accent: "#bb9af7" },
	{ id: "mono", nameKey: "themeMono", bg: "#fafafa", accent: "#2d2d2d" },
];

function current(): string {
	const t = document.documentElement.dataset.theme || "dark";
	return THEMES.some((x) => x.id === t) ? t : "dark";
}

export function ThemeToggle() {
	const lang = useStore((s) => s.lang);
	const [theme, setTheme] = useState<string>(current);
	const [open, setOpen] = useState(false);
	const btnRef = useRef<HTMLButtonElement | null>(null);
	const menuRef = useRef<HTMLDivElement | null>(null);
	const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);

	useEffect(() => {
		setTheme(current());
	}, []);

	// Close on outside click / Escape. The menu is PORTALED to <body> (not a
	// child of the button), so the outside check must exclude the menu too —
	// otherwise mousedown on a menu item closes the menu before its onClick
	// fires and the theme never switches.
	useEffect(() => {
		if (!open) return;
		const onDown = (e: MouseEvent) => {
			const t = e.target as Node;
			if (!btnRef.current?.contains(t) && !menuRef.current?.contains(t)) {
				setOpen(false);
			}
		};
		const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
		window.addEventListener("mousedown", onDown);
		window.addEventListener("keydown", onKey);
		return () => {
			window.removeEventListener("mousedown", onDown);
			window.removeEventListener("keydown", onKey);
		};
	}, [open]);

	const pick = (id: string) => {
		document.documentElement.dataset.theme = id;
		try {
			localStorage.setItem(KEY, id);
		} catch {
			/* private mode — runtime flip still works */
		}
		setTheme(id);
		setOpen(false);
		void applyStatusBarTheme();
	};

	const toggleOpen = () => {
		const r = btnRef.current?.getBoundingClientRect();
		if (r) setPos({ left: r.left, bottom: window.innerHeight - r.top + 6 });
		setOpen((v) => !v);
	};

	return (
		<>
			<button
				ref={btnRef}
				type="button"
				onClick={toggleOpen}
				title={t("themeTitle", lang)}
				aria-label={t("selectTheme", lang)}
				className="press-down group flex items-center justify-center w-7 h-7 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors duration-150"
			>
				<Palette
					size={14}
					className="transition-transform duration-300 group-hover:rotate-12"
				/>
			</button>
			{open &&
				pos &&
				createPortal(
					<div
						ref={menuRef}
						className="fixed z-[90] w-44 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-2xl p-1"
						style={{ left: pos.left, bottom: pos.bottom }}
						role="menu"
						aria-label={t("themeList", lang)}
					>
						{THEMES.map((th) => (
							<button
								key={th.id}
								type="button"
								onClick={() => pick(th.id)}
								className="w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg text-[12px] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/50"
							>
								<span
									className="w-5 h-5 rounded-full border border-[var(--color-line)] flex-shrink-0 grid place-items-center"
									style={{ background: th.bg }}
								>
									<span
										className="w-2 h-2 rounded-full"
										style={{ background: th.accent }}
									/>
								</span>
								<span className="flex-1 text-left">{t(th.nameKey, lang)}</span>
								{theme === th.id && (
									<Check size={13} className="text-[var(--color-accent)]" />
								)}
							</button>
						))}
					</div>,
					document.body,
				)}
		</>
	);
}
