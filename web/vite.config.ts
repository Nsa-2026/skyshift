import { defineConfig, searchForWorkspaceRoot } from "vite";

// Static first: /data/<slug>/ is served from public/data. /api/** goes to the local
// FastAPI server in dev (cd server && uv run uvicorn ... --port 8000); in production
// Firebase Hosting rewrites /api/** to Cloud Run.
// fs.allow: dev only, so src/review can fall back to fixtures/api/candidates.json (a visible
// "Sample data" list) until /data/candidates.json is published. Builds never include it.
export default defineConfig({
  server: {
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../fixtures/api"] },
    proxy: { "/api": { target: process.env.SKYSHIFT_API ?? "http://localhost:8000", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
  },
} as never);
