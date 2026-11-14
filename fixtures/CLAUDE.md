# fixtures/CLAUDE.md

Owner: P1. Small samples for tests, and for the front end until real endpoints exist.

- api/   SYNTHETIC examples of every shape in contracts/api.md, made by scripts/make_fixtures.py
         (python3 -I scripts/make_fixtures.py, stdlib only). Every file has "synthetic": true and a note.
         Never copy them into web/public/data/ and never show them as real data.
  - frames_barnard.json   Months clock: Barnard's Star-like, 3 visits, D2, 48×48 at 2.05″/px,
                          stepping 5.2″ north per visit. Frame data in frames/barnard/*.bin.
  - frames_comet.json     Hours clock: a comet-like mover (3I/ATLAS rate, 176″/h), 6 exposures,
                          D3, 48×48 at 6.15″/px. Frame data in frames/comet/*.bin.
  - object_barnard.json, gaia_barnard.json, known_comet.json,
    resolve_barnard.json, resolve_coords.json, validation.json, suspects.json.
  - candidates.json: the exception, a REAL sample ("synthetic": false) of web/public/data/candidates.json,
    written by the exporter (cd pipeline && uv run skyshift-candidates --fixture ../fixtures/api/candidates.json):
    the best few and the last of each kind per zone; counts are the full file's.
  - Frame files: raw little-endian uint16, north up, east left; value = raw × scale + zero (MJy/sr);
    data_url is relative to the JSON file.
- fits/  Small real cutouts with IMAGE, FLAGS and WCS-WAVE, for core/ and pipeline/ tests (P1 adds them
         after the first photo). Keep each file small; whole photos never go in Git.

When contracts/api.md changes, change scripts/make_fixtures.py and regenerate in the same PR.
