import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(here, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
});
