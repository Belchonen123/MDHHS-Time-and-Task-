import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

const BACKEND_ORIGIN =
  process.env.VITE_BACKEND_PROXY ?? "http://127.0.0.1:8001"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3456,
    // Default Vite on Windows sometimes binds [::1]:port only → http://127.0.0.1:3456
    // refuses while http://localhost:3456 works (or vice versa). Listening on all
    // addresses fixes both; stays local-use only unless you intentionally share the URL.
    host: true,
    proxy: {
      "/api": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
  // `npm run preview` does not inherit `server.proxy` — without this, `/api/*`
  // never hits FastAPI and blob downloads show "Failed to fetch".
  preview: {
    proxy: {
      "/api": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
})
