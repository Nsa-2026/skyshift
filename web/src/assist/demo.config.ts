// Dev only: serves the assist demo, which reads the real pilot sample in ../fixtures/api/.
//   cd web && npx vite --config src/assist/demo.config.ts --port 5199
//   open http://localhost:5199/src/assist/demo.html
// Not used by `npm run build` (the site's entry is index.html with the default vite.config.ts).
import { defineConfig } from "vite";

export default defineConfig({
  root: new URL("../..", import.meta.url).pathname,
  server: { fs: { allow: [new URL("../../..", import.meta.url).pathname] } },
});
