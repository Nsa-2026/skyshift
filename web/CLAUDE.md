# web/CLAUDE.md
Vite + TypeScript, no heavy framework unless agreed. Two owners:
- P3: src/app, src/sky, src/card, src/api, src/data, src/score, index.html, styles.
- P4: src/blink, src/vote.
Don't edit the other owner's folders.
- Data: src/api tries /data/<slug>/*.json first, then /api/*. Shapes from contracts/api.md.
- Aladin Lite 3.8.2 from https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js, unpkg as second. Layers by IRSA URL only. showSimbadPointerControl: false. No gotoObject and no Aladin catalogFrom* loaders (Simbad, VizieR or any other): known objects come from /api/known (MPC orbits with kete).
- The blink must work without Aladin (no WebGL2, or tiles down).
- Real buttons with aria-pressed or aria-selected; works at phone width.
- Tests: npm test.
- Until real data exists, use the synthetic examples in fixtures/api/. Never copy them into public/data/.
- public/data/ is written by scripts/precompute (P2) and scripts/validate (P1). Don't hand-edit it.

## P4: blink and votes
P4 works only in web/src/blink, web/src/vote and pitch/.
- Frames are 16-bit arrays with scale and zero (contracts/api.md): value = raw × scale + zero, in MJy/sr. Stretch, invert, subtract and add happen here, in the browser.
- Draw with image-rendering: pixelated. Preload every epoch before playing.
- Months blinks visits; Hours blinks exposures. Never blink different detectors as motion.
- Firestore: candidates and votes, fields exactly as contracts/api.md. Rules are create-only. voter is a random id kept in the browser.
- Mount through openBlink(ra, dec, fov) from src/app.
