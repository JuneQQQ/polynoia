import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
    plugins: [react(), tailwindcss()],
    build: {
        rollupOptions: {
            output: {
                // Bucket heavy vendor libs into their own chunks so they cache
                // independently + load in parallel. The lazy() boundaries (CodeEditor,
                // doc renderers, diff views) already defer these off the boot path;
                // this keeps each lazy chunk clean + the eager markdown/highlight stack
                // separate from app code.
                manualChunks: function (id) {
                    if (!id.includes("node_modules"))
                        return;
                    if (/codemirror|@uiw|@replit/.test(id))
                        return "cm";
                    if (/highlight\.js|rehype|remark|react-markdown/.test(id))
                        return "md";
                    if (/@git-diff-view/.test(id))
                        return "diff";
                    if (/milkdown|@marp|marp-|xlsx|docx-preview|pptx-preview|pptxgenjs/.test(id))
                        return "docs";
                    if (/framer-motion|^.*\/motion\//.test(id))
                        return "motion";
                },
            },
        },
    },
    server: {
        port: 5173,
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
