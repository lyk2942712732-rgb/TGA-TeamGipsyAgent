import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves only the SPA, but the browser bundle resolves its API
// base from the page origin.  Without this proxy every request from the dev
// server hits :5173 where no API is mounted.  The bundled `tga go` launcher is
// unaffected: it serves the built SPA and /api from one origin.
const apiTarget = process.env.TGA_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
    plugins: [react()],
    server: {
        host: "127.0.0.1",
        port: 5173,
        proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    },
    preview: {
        host: "127.0.0.1",
        port: 4173,
        proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    },
    build: {
        rollupOptions: {
            output: {
                manualChunks: {
                    react: ["react", "react-dom", "react-router-dom"],
                    flow: ["@xyflow/react"],
                },
            },
        },
    },
});
