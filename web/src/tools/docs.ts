// What the Tools page says about the API (contracts/api.md v3, server/src/skyshift_server/app.py)
// and how to cite (README.md, "Acknowledgements": copied verbatim). Data, not markup.

/** The public site. On localhost the examples still point at it: they are meant to be run elsewhere. */
export const PUBLIC_SITE = "https://skyshift.web.app/";
export const REPO_URL = "https://github.com/cucuradu/skyshift";

export function siteBase(loc: Pick<Location, "hostname" | "origin"> | undefined = globalThis.location): string {
  if (!loc || /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])$/.test(loc.hostname) || !/^https?:/.test(loc.origin)) {
    return PUBLIC_SITE;
  }
  return `${loc.origin}/`;
}

export interface Param {
  name: string;
  /** Unit or allowed values. */
  unit: string;
  /** "required" or the default. */
  need: string;
  meaning: string;
}

export interface Endpoint {
  id: string;
  path: string;
  summary: string;
  params: Param[];
  /** Example query, in order; numbers stay numbers in the Python snippet. */
  example: [string, string | number][];
  exampleNote: string;
  /** Response fields: [name, meaning]. */
  fields: [string, string][];
  note?: string;
  /** Seconds a cold call may take, for the snippet's timeout. */
  timeout: number;
  /** Extra lines for the curl snippet. */
  curlMore?: (url: string) => string;
  /** Extra Python after `data = r.json()`. */
  pyMore?: string;
}

const RA: Param = { name: "ra", unit: "deg, ICRS", need: "required", meaning: "Right ascension, 0° to 360°" };
const DEC: Param = { name: "dec", unit: "deg, ICRS", need: "required", meaning: "Declination, −90° to +90°" };

export const ENDPOINTS: Endpoint[] = [
  {
    id: "frames",
    path: "/api/frames",
    summary:
      "Blink frames for one spot, on one clock and one detector. Each epoch (a visit on Months, one exposure on Hours) gives its times, wavelengths, flag fractions and a link to its pixels.",
    params: [
      RA,
      DEC,
      { name: "clock", unit: "months or hours", need: "months", meaning: "Months: one median stack per visit. Hours: single exposures of one visit, in time order" },
      { name: "fov", unit: "arcmin", need: "5 (months), 20 (hours)", meaning: "Field: 5 or 15 on Months (2.05″ or 3.08″ pixels); 20 or 60 on Hours (6.15″ or 12.3″ pixels)" },
      { name: "det", unit: "1 to 6", need: "2", meaning: "Detector: D1 0.75–1.1 µm, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm" },
    ],
    example: [
      ["ra", 133.79513],
      ["dec", -7.24515],
      ["clock", "months"],
      ["fov", 5],
      ["det", 6],
    ],
    exampleNote: "The challenge's own position, 08h55m10.831s −7d14m42.53s: WISE 0855−0714, which shows in D5 and D6 only.",
    fields: [
      ["clock, center", "As asked; center is {ra, dec} in degrees, at pixel ((nx−1)/2, (ny−1)/2)"],
      ["pixel_arcsec, size", "Pixel size in arcsec; [nx, ny], each at most 300"],
      ["epochs[]", "id, kind (visit or exposure), mjd_mid, mjd_start, mjd_end (MJD, UTC, from each exposure's MJD-AVG), det, um_min and um_max (µm), n_exposures, flag_frac and sat_frac (0 to 1), data_url, scale, zero; center {ra, dec} only when the frames follow a moving object"],
      ["gaia[]", "Gaia DR3 stars in the frame at J2016.0: id (a string), ra, dec (deg), pmra and pmdec (mas/yr, pmra × cos dec), parallax (mas), g (mag)"],
      ["known[]", "Known asteroids and comets at an epoch's mjd_mid: name, epoch_id, ra, dec (deg), v (mag), source (mpc, most or irsa-3i)"],
      ["data_release, archive_checked", "Collections searched (e.g. DR1+QR3) and the date of the IRSA search"],
    ],
    note:
      "Pixels: data_url, resolved against the JSON's URL, holds nx × ny little-endian uint16 values, row by row, north up and east left. Value = raw × scale + zero, in MJy/sr; raw 0 means no data. To fit in 60 s the live API stacks at most 6 exposures per visit (Months) and serves at most 6 exposures (Hours); the static files of the featured targets use all of them.",
    timeout: 60,
    curlMore: (url) =>
      `# one frame's pixels (needs jq): nx × ny little-endian uint16\ncurl -s "${new URL("/", url).href.replace(/\/$/, "")}$(curl -s "${url}" | jq -r '.epochs[0].data_url')" -o frame.bin`,
    pyMore: `import numpy as np
from urllib.parse import urljoin

nx, ny = data["size"]
for e in data["epochs"]:
    raw = np.frombuffer(requests.get(urljoin(r.url, e["data_url"]), timeout=60).content, dtype="<u2").reshape(ny, nx)
    img = np.where(raw == 0, np.nan, raw * e["scale"] + e["zero"])  # MJy/sr, north up, east left
    print(e["id"], e["mjd_mid"], e["n_exposures"], np.nanmedian(img))`,
  },
  {
    id: "object",
    path: "/api/object",
    summary:
      "The object nearest a position (within 6″): its name, Gaia entry, SPHEREx spectrum, and the motion measured between its first and last visit.",
    params: [RA, DEC],
    example: [
      ["ra", 269.4462],
      ["dec", 4.76883],
    ],
    exampleNote: "Barnard's Star.",
    fields: [
      ["name, gaia, known_ucd", "Built-in or catalogue name, or null; one Gaia entry as in frames.gaia, or null; known ultracool dwarf (null so far)"],
      ["spectrum[]", "um (µm), flux_ujy and err_ujy (µJy), det, mjd (the exposure's MJD-AVG, UTC), visit, saturated (OVERFLOW or NONLINEAR in the core); sorted by wavelength"],
      ["spectrum_reason", "Why the spectrum is empty (saturated or no_data), else null"],
      ["measure", "shift_arcsec (″), pa_deg (° east of north), dt_yr (yr), rate_arcsec_yr (″/yr), sigma_arcsec (″, 1σ), n_ref_stars, predicted_arcsec (″, from Gaia, or null). Null with fewer than two visits, or when no single source stands out clearly in both"],
      ["measure_reason", "Why measure is null (few_visits, not_detected, edge, different_sources, no_time, moved_away), else null"],
      ["measure_hint", "With moved_away: { name, ra, dec, mjd }, the fast star catalogued here and where it is during SPHEREx's visits, else null"],
    ],
    timeout: 60,
    pyMore: `m = data["measure"]
if m:
    print(f'{m["shift_arcsec"]:.2f} ± {m["sigma_arcsec"]:.2f} arcsec in {m["dt_yr"]:.3f} yr; Gaia predicts {m["predicted_arcsec"]}')`,
  },
  {
    id: "resolve",
    path: "/api/resolve",
    summary:
      "Turns what people type into a position: any coordinate format, or a built-in name (the five test objects and comet 3I/ATLAS, with common aliases). There is no SIMBAD lookup.",
    params: [
      {
        name: "q",
        unit: "text",
        need: "required",
        meaning: "08h55m10.831s -7d14m42.53s, 08:55:10.8 -07:14:42, 133.795 -7.245, J085510.83-071442.5, or a built-in name",
      },
    ],
    example: [["q", "08h55m10.831s -7d14m42.53s"]],
    exampleNote: "The challenge's own string.",
    fields: [
      ["q, name", "What you sent; the built-in name, or null"],
      ["ra, dec", "Degrees, ICRS. Built-in fast stars are moved to the present"],
      ["slug, source", "The featured target's folder under /data/, or null; coords or builtin"],
    ],
    note: "400 bad_request if q can't be read; 404 not_found for a name that isn't built in.",
    timeout: 30,
  },
  {
    id: "gaia",
    path: "/api/gaia",
    summary: "Gaia DR3 stars in a cone, at Gaia's epoch J2016.0 (MJD 57388.5).",
    params: [RA, DEC, { name: "r", unit: "arcmin", need: "required", meaning: "Radius, over 0 and at most 60" }],
    example: [
      ["ra", 269.4462],
      ["dec", 4.76883],
      ["r", 2],
    ],
    exampleNote: "Two arcminutes around Barnard's Star.",
    fields: [
      ["gaia[]", "id (a string: Gaia ids exceed 2⁵³), ra, dec (deg), pmra and pmdec (mas/yr, pmra × cos dec), parallax (mas), g (mag); each may be null"],
    ],
    timeout: 60,
  },
  {
    id: "known",
    path: "/api/known",
    summary: "Known asteroids and comets predicted in a cone at one moment.",
    params: [
      RA,
      DEC,
      { name: "r", unit: "arcmin", need: "required", meaning: "Radius, over 0 and at most 60" },
      { name: "mjd", unit: "MJD, UTC", need: "required", meaning: "When: 33282 (1900) to 88069 (2100)" },
    ],
    example: [
      ["ra", 166.82745],
      ["dec", 5.27872],
      ["r", 10],
      ["mjd", 61023.6],
    ],
    exampleNote: "Comet 3I/ATLAS during SPHEREx's sequence of 14 Dec 2025.",
    fields: [
      ["mjd", "As asked"],
      ["known[]", "name, ra, dec (deg), v (mag, or null), source (mpc: Minor Planet Center orbits computed with kete; most: IRSA MOST; irsa-3i: IRSA's 3I/ATLAS list); epoch_id is null here"],
    ],
    timeout: 60,
  },
];

export function exampleUrl(ep: Endpoint, base: string): string {
  const p = new URLSearchParams(ep.example.map(([k, v]) => [k, String(v)]));
  return new URL(`${ep.path.replace(/^\//, "")}?${p.toString().replace(/\+/g, "%20")}`, base).href;
}

export function curlSnippet(ep: Endpoint, base: string): string {
  const url = exampleUrl(ep, base);
  return [`curl -s "${url}"`, ep.curlMore?.(url)].filter(Boolean).join("\n\n");
}

const pyValue = (v: string | number) => (typeof v === "number" ? String(v) : JSON.stringify(v));

export function pythonSnippet(ep: Endpoint, base: string): string {
  const params = ep.example.map(([k, v]) => `"${k}": ${pyValue(v)}`).join(", ");
  const lines = [
    "import requests",
    "",
    "r = requests.get(",
    `    "${new URL(ep.path.replace(/^\//, ""), base).href}",`,
    `    params={${params}},`,
    `    timeout=${ep.timeout},${ep.id === "frames" || ep.id === "object" ? "  # a spot nobody asked for yet takes up to 48 s" : ""}`,
    ")",
    "r.raise_for_status()",
    "data = r.json()",
  ];
  if (ep.pyMore) lines.push("", ep.pyMore);
  return lines.join("\n");
}

export const STATIC_FILES: [string, string][] = [
  ["/data/<slug>/index.json", "Which frame sets a featured target has: clock, field, detectors, epochs, bytes"],
  ["/data/<slug>/frames_<clock>_<fov>.json", "/api/frames for one clock and field, with the epochs of several detectors (filter by det); pixel files in frames/ next to it"],
  ["/data/<slug>/frames.json", "The target's own clock and field"],
  ["/data/<slug>/object.json", "/api/object"],
  ["/data/candidates.json", "Candidates for review (the downloads above)"],
  ["/data/validation.json", "The checks V1–V11 behind the Results page"],
  ["/data/suspects.json", "Movers from the pipeline zone with no known asteroid within 30″"],
];

export function staticPython(base: string): string {
  return `import requests

base = "${base.replace(/\/$/, "")}"
idx = requests.get(f"{base}/data/wise0855/index.json", timeout=30).json()
for s in idx["frames"]:
    print(s["file"], s["clock"], s["fov"], "arcmin, detectors", s["dets"])
frames = requests.get(f"{base}/data/wise0855/{idx['own']}", timeout=30).json()`;
}

export function embedSnippet(base: string): string {
  const src = new URL("?ra=133.79513&dec=-7.24515&clock=months&fov=5&det=6&embed=1#/explore", base).href.replace(/&/g, "&amp;");
  return `<iframe src="${src}"
  title="SkyShift: blink of WISE 0855−0714 in SPHEREx" width="100%" height="760"
  style="border:0" loading="lazy"></iframe>`;
}

// ---- citation (README.md, "Acknowledgements", verbatim) --------------------------------------

export const ACK_SPHEREX =
  "This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.";
export const ACK_IRSA =
  "This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology.";
export const ACK_GAIA =
  "This work has made use of data from the European Space Agency (ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.";

export const DATASETS: { name: string; doi: string }[] = [
  { name: "DR1 Spectral Images", doi: "10.26131/IRSA678" },
  { name: "QR3 Spectral Images", doi: "10.26131/IRSA662" },
  { name: "Spectral All-Sky Cube", doi: "10.26131/IRSA679" },
];

export interface Paper {
  role: string;
  text: string;
  bibcode: string;
}

/** SPHEREx canonical papers, as README.md lists them (from IRSA's SPHEREx mission page). */
export const SPHEREX_PAPERS: Paper[] = [
  { role: "Mission", text: "Bock et al. 2026, ApJ 999, 139", bibcode: "2026ApJ...999..139B" },
  { role: "Pipeline", text: "Akeson et al. 2026, ApJS 285, 67", bibcode: "2026ApJS..285...67A" },
  { role: "Instrument and calibration", text: "Korngut et al. 2026, arXiv:2603.29835", bibcode: "2026arXiv260329835K" },
  { role: "Sky simulator", text: "Crill et al. 2025, ApJS 281, 10", bibcode: "2025ApJS..281...10C" },
];

export const GAIA_PAPERS: Paper[] = [
  { role: "The mission", text: "Gaia Collaboration, Prusti et al. 2016, A&A 595, A1", bibcode: "2016A&A...595A...1G" },
  { role: "Data Release 3", text: "Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1", bibcode: "2023A&A...674A...1G" },
];

export const adsAbs = (bibcode: string) => `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(bibcode)}/abstract`;
export const adsBibtex = (bibcode: string) => `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(bibcode)}/exportcitation`;

export function skyshiftBibtex(accessed: Date = new Date()): string {
  const day = accessed.toISOString().slice(0, 10);
  // No column alignment: the page sets code in Archivo (no monospace), where padding would look ragged.
  return `@software{skyshift2026,
  author = {{SkyShift team}},
  title = {{SkyShift}: blink {NASA}'s {SPHEREx} sky and measure what moved},
  year = {2026},
  month = nov,
  url = {${REPO_URL}},
  howpublished = {\\url{${PUBLIC_SITE}}},
  note = {NASA Space Apps Challenge 2026, challenge \`\`Planet X and SPHEREx''. SPHEREx data DR1 and QR3 from NASA/IPAC IRSA},
  license = {Apache-2.0},
  urldate = {${day}}
}`;
}
