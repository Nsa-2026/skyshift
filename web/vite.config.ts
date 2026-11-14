import { defineConfig, searchForWorkspaceRoot } from "vite";

// Static first: /data/<slug>/ is served from public/data. /api/** goes to the local
// FastAPI server in dev (bash scripts/run-local.sh, port 8083); in production
// Firebase Hosting rewrites /api/** to Cloud Run.
// fs.allow: dev only, so src/review can fall back to fixtures/api/candidates.json (a visible
// "Sample data" list) until /data/candidates.json is published. Builds never include it.
export default defineConfig({
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../fixtures/api"] },
    proxy: { "/api": { target: process.env.SKYSHIFT_API ?? "http://127.0.0.1:8083", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
  },
} as never);
