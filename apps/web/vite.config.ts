import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.TGA3_API_PROXY_TARGET ?? "http://127.0.0.1:8083";

export default defineConfig({
    plugins: [react()],
    server: {
        host: "127.0.0.1",
        port: 5173,
        proxy: { "/api/v3": { target: apiTarget, changeOrigin: true } },
    },
    preview: {
        host: "127.0.0.1",
        port: 4173,
        proxy: { "/api/v3": { target: apiTarget, changeOrigin: true } },
    },
    build: {
        rollupOptions: {
            output: {
                manualChunks: {
                    react: ["react", "react-dom", "react-router-dom"],
                },
            },
        },
    },
});
