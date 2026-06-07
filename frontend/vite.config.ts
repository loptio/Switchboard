/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: the browser only ever talks to the Vite origin, so the session +
// csrftoken cookies are same-origin and "just work" (no backend CORS needed
// locally). Client code calls `${VITE_API_BASE}` which defaults to "/api"; the
// proxy forwards "/api/*" to the API and strips the prefix. Override the target
// with VITE_API_TARGET if the API runs somewhere other than localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    css: true,
  },
});
