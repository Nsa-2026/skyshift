# core/CLAUDE.md
Owner: P1. Python package skyshift_core, managed with uv.
Shared science code, imported by server/ and pipeline/. No web or cloud code here.
- visits(mjds), sessions(mjds): the visit rule in contracts/api.md.
- bad_pixel_mask(flags): exactly the 'mask' bits in contracts/flags.md. Never SOURCE or FULLSAMPLE.
- detect, centroid (wings when saturated), shift between visit stacks, aperture photometry in µJy.
- Time = MJD-AVG (UTC). Detector = energy_bandpassname or header DETECTOR, never the file name. IMAGE in MJy/sr; 1 MJy/sr = 889 µJy per 6.15″ pixel.
- Pure functions: numpy in, numpy or dicts out. Tests: uv run pytest on fixtures/fits/.
- Header facts from the first file: docs/research/2026-11-14-first-photo-facts.md (DETECTOR 1–6, 6.152″/px, PSF FWHM 7.53″, FLAGS is tile-compressed).
