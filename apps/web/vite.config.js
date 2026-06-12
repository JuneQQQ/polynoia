import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
    plugins: [react(), tailwindcss()],
    build: {
        rollupOptions: {
            output: {
                // Bucket heavy vendor libs into their own chunks so they cache
                // independently + load in parallel.
                // - "cm" (CodeMirror ~2.3MB) is now OFF the boot path: SourcePreview is
                //   lazy()'d in DocPreviewPane (it was the one static import pulling the
                //   whole CodeMirror stack eager). Loads only when a source file is
                //   previewed / the code editor opens.
                // - "md" (react-markdown + rehype-highlight ~2.3MB) is STILL eager: TextPart
                //   is in the always-loaded PARTS_REGISTRY and statically imports it. TODO:
                //   lazy-load the markdown renderer behind a plain-text fallback to defer it
                //   too (the remaining big first-paint chunk).
                manualChunks: function (id) {
                    if (!id.includes("node_modules"))
                        return;
                    if (/codemirror|@uiw|@replit/.test(id))
                        return "cm";
                    if (/highlight\.js|rehype|remark|react-markdown/.test(id))
                        return "md";
                    if (/@git-diff-view/.test(id))
                        return "diff";
                    // NOTE: the doc-renderer libs (milkdown / marp / xlsx / docx / pptx)
                    // were forced into a single "docs" chunk, which created a cross-chunk
                    // circular init → "Cannot access 'X' before initialization" at boot in
                    // production builds (React never mounted → black screen in the packaged
                    // .app; dev's unbundled ESM masked it). Let Rollup chunk them along
                    // their lazy() dynamic-import boundaries instead.
                    if (/framer-motion|^.*\/motion\//.test(id))
                        return "motion";
                },
            },
        },
    },
    server: {
        port: 7788,
        // Fail loudly if 7788 is taken instead of silently drifting to 7789 — the
        // desktop (tauri.conf.json devUrl) and the proxy pin this exact port, and a
        // drift would leave 7788 free for an agent's sandboxed dev server to grab,
        // hijacking the desktop webview (the "缺陷管理项目 顶掉桌面端" bug).
        strictPort: true,
        host: "0.0.0.0",
        proxy: {
            "/api": "http://localhost:7780",
            "/ws": {
                target: "ws://localhost:7780",
                ws: true,
            },
        },
    },
});
