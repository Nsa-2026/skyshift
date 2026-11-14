import { defineConfig } from "vite";

// Static first: /data/<slug>/ is served from public/data. /api/** goes to the local
// FastAPI server in dev (cd server && uv run uvicorn ... --port 8000); in production
// Firebase Hosting rewrites /api/** to Cloud Run.
export default defineConfig({
  server: {
    proxy: { "/api": { target: process.env.SKYSHIFT_API ?? "http://localhost:8000", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
  },
} as never);
