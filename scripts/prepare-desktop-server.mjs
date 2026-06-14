// Cross-platform replacement for prepare_desktop_server_resource.sh.
//
// Copies the Python backend SOURCE (not its .venv) into the Tauri bundle's
// resources/server. The desktop runtime runs it with `uv run`, materializing a
// per-app virtualenv under the user's app-data dir on first launch — so the
// bundle stays small and the env is the user's, not the build machine's.
//
// Why Node instead of the bash script: the shell version uses `rsync`, which
// isn't available on Windows. Tauri builds the Windows setup.exe on Windows, so
// the `beforeBuildCommand` must run there too. Node ships with the toolchain and
// `fs.cp` gives us recursive copy + filter on every platform. The bash script is
// kept for anyone already wired to it; this is the canonical cross-platform path.
//
// Self-locates the repo root from its own path, so it works regardless of the
// caller's cwd.

import { existsSync } from "node:fs";
import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const SRC = join(repoRoot, "apps", "server");
const DST = join(repoRoot, "apps", "desktop", "src-tauri", "resources", "server");

// Names excluded anywhere in the tree — build/runtime artifacts that must not
// ship (the user's env is built fresh on their machine).
const EXCLUDE_DIRS = new Set([".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"]);
const EXCLUDE_EXT = [".db", ".db-shm", ".db-wal", ".pyc"];

function keep(src) {
  const base = src.replaceAll("\\", "/").split("/").pop() ?? "";
  if (EXCLUDE_DIRS.has(base)) return false;
  if (EXCLUDE_EXT.some((ext) => base.endsWith(ext))) return false;
  return true;
}

async function main() {
  if (!existsSync(join(SRC, "polynoia", "main.py"))) {
    throw new Error(`backend source not found at ${SRC} (expected polynoia/main.py)`);
  }

  await rm(DST, { recursive: true, force: true });
  await mkdir(DST, { recursive: true });

  // Mirror the bash script's explicit allowlist: pyproject.toml + uv.lock + the
  // polynoia package. Nothing else from apps/server is needed at runtime.
  for (const entry of ["pyproject.toml", "uv.lock", "polynoia"]) {
    const from = join(SRC, entry);
    if (!existsSync(from)) {
      if (entry === "uv.lock") continue; // optional but strongly recommended
      throw new Error(`required backend file missing: ${from}`);
    }
    await cp(from, join(DST, entry), { recursive: true, filter: keep });
  }

  console.log(`Prepared desktop backend resource: ${DST}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
