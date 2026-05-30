/** ThemeToggle — Atelier dark ⇄ light.
 *
 * Self-contained on purpose: theme is a pure presentation concern, so it
 * lives in the DOM (`<html data-theme>`) + localStorage, NOT in the Zustand
 * store. The pre-paint restore script is in index.html; this just flips +
 * persists. No business logic touched.
 */
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

const KEY = "polynoia-theme";
type Theme = "dark" | "light";

function current(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(current);

  // Keep state in sync if some other surface flips the attribute.
  useEffect(() => {
    setTheme(current());
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // private mode / storage disabled — runtime flip still works
    }
    setTheme(next);
  };

  const goingTo = theme === "dark" ? "亮色" : "暗色";
  return (
    <button
      type="button"
      onClick={toggle}
      title={`切换到${goingTo}主题`}
      aria-label={`切换到${goingTo}主题`}
      className="press-down group flex items-center justify-center w-7 h-7 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors duration-150"
    >
      {theme === "dark" ? (
        <Sun size={14} className="transition-transform duration-300 group-hover:rotate-45" />
      ) : (
        <Moon size={14} className="transition-transform duration-300 group-hover:-rotate-12" />
      )}
    </button>
  );
}
