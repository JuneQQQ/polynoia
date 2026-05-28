import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
    plugins: [react(), tailwindcss()],
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
