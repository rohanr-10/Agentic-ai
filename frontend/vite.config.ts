/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => {
  // Load .env for VITE_BACKEND_ORIGIN (used by the dev/preview proxy).
  const env = loadEnv(mode, process.cwd(), "");
  const backendOrigin = env.VITE_BACKEND_ORIGIN || "http://127.0.0.1:8000";

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    // Same-origin proxy: the frontend talks to /api/** relative to its own
    // origin, so no backend CORS configuration is needed in development or
    // preview. `ws: true` covers the WebSocket route /api/v1/ws.
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: backendOrigin,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      port: 4173,
      proxy: {
        "/api": {
          target: backendOrigin,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    build: {
      target: "es2022",
      sourcemap: false,
    },
    test: {
      environment: "jsdom",
      globals: true,
      css: false,
      setupFiles: ["./tests/setup.ts"],
      include: ["tests/**/*.test.{ts,tsx}"],
      restoreMocks: true,
    },
  };
});
