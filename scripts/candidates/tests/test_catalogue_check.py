"""scripts/candidates/catalogue_check.py (no network: IRSA's answers are stubbed). Runs from server/ or pipeline/:

    cd server && uv run pytest ../scripts/candidates/tests
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalogue_check as K  # noqa: E402

RA, DEC, MJD = 150.0, 10.0, 61000.0


def at(de: float, dn: float, ra: float = RA, dec: float = DEC) -> tuple[float, float]:
    """A position de, dn arcsec east/north of (ra, dec)."""
    return ra + de / 3600.0 / math.cos(math.radians(dec)), dec + dn / 3600.0


def cand(cid="z-sp-1", kind="spectral", labels=("brown_dwarf_like",), det=4, flux=1000.0, **kw) -> dict:
    return {"id": cid, "kind": kind, "ra": RA, "dec": DEC, "mjd": MJD, "det": det, "flux_ujy": flux, "zone": "z",
            "labels": list(labels), "gaia_match": False, "pm_mas_yr": None, "pa_deg": None, **kw}


def tmass(de=0.3, dn=0.0, j=15.0, h=14.3, k=13.8, q="AAA", jd=2451300.5):
    ra, dec = at(de, dn)
    return {"designation": "x", "ra": ra, "dec": dec, "j_m": j, "h_m": h, "k_m": k, "ph_qual": q, "jdate": jd,
            "j_msigcom": 0.05, "h_msigcom": 0.05, "k_msigcom": 0.05, "gal_contam": 0}


def catwise(de=0.2, dn=0.0, w1=13.0, w2=12.5):
    ra, dec = at(de, dn)
    return {"source_name": "J1", "ra": ra, "dec": dec, "ra_pm": None, "dec_pm": None, "pmra": None, "pmdec": None,
            "sigpmra": None, "sigpmdec": None, "w1mpro": w1, "w1sigmpro": 0.03, "w2mpro": w2, "w2sigmpro": 0.03,
            "meanobsmjd": 57170.0}


def allwise(de=0.2, dn=0.0, w1=13.0, w2=12.5, w3=12.0, w3snr=2.0, ext="0"):
    ra, dec = at(de, dn)
    return {"designation": "J1", "ra": ra, "dec": dec, "w1mpro": w1, "w2mpro": w2, "w3mpro": w3, "w3snr": w3snr,
            "w4mpro": None, "w4snr": None, "ext_flg": ext, "ph_qual": "AAU"}


def gaia(de=0.0, dn=0.0, g=20.5, bp_rp=None, pmra=None, pmdec=None, ra=None, dec=None, sid=1):
    r, d = at(de, dn)
    return {"source_id": sid, "ra": r if ra is None else ra, "dec": d if dec is None else dec, "pmra": pmra, "pmdec": pmdec,
            "parallax": None, "parallax_error": None, "phot_g_mean_mag": g, "bp_rp": bp_rp,
            "in_galaxy_candidates": 0, "in_qso_candidates": 0}


def xsc(de=0.5, dn=0.0, k=13.0):
    ra, dec = at(de, dn)
    return {"designation": "g", "ra": ra, "dec": dec, "r_k20fe": 9.0, "j_m_k20fe": 14.0, "k_m_k20fe": k, "jdate": 2451300.5}


def ans(**kw):
    base = {k: [] for k in K.CATALOGUES}
    base.update(kw)
    return base


def checks(c, a, own=None):
    return {x["code"]: x for x in K.candidate_checks(c, a, own)}


def test_mover_catalogue_star_within_6_arcsec_only():
    c = cand("z-1-1", kind="mover", labels=())
    got = K.candidate_checks(c, ans(tmass=[tmass(de=4.0, dn=0.0, j=16.2, h=15.6, k=15.2)]))
    assert len(got) == 1 and got[0]["code"] == "catalogue_star" and got[0]["level"] == "warn"
    assert "2MASS lists a J 16.2, H 15.6, Ks 15.2 source 4.0″ east of its middle position" in got[0]["text"]
    assert got[0]["values"]["sep_arcsec"] == 4.0 and got[0]["values"]["j_mag"] == 16.2
    assert "hours clock in D4" in got[0]["look"]
    assert K.candidate_checks(c, ans(tmass=[tmass(de=7.0)])) == []
    # the zone's chance of a source that close: 1 2MASS source per 6–10″ ring → ρ = 1 / (π·64) per arcsec²
    others = [cand(f"z-1-{i}", kind="mover", labels=()) for i in range(2, 6)]
    a = {o["id"]: ans(tmass=[tmass(de=8.0)]) for o in others}
    dens = K.zone_density(others, a)["z"]
    assert abs(dens["tmass"] - 1 / (math.pi * 64)) < 1e-12
    got = K.candidate_checks(c, ans(tmass=[tmass(de=4.0)]), density=dens)[0]
    assert got["values"]["chance"] == round(1 - math.exp(-16 / 64), 3) and "22% of spots here" in got["text"]


def test_mover_gaia_star_moved_to_the_mover_epoch():
    """A Gaia star listed 8″ away in 2016 that moves 1″/yr toward the mover is 1.6″ from it in 2025."""
    c = cand("z-1-1", kind="mover", labels=())
    yr = (MJD - K.GAIA_MJD) / 365.25
    ra, dec = at(-1000.0 * yr / 1000.0 - 1.6, 0.0)  # its 2016 place, west; it moves east at 1000 mas/yr
    got = K.candidate_checks(c, ans(gaia=[gaia(ra=ra, dec=dec, pmra=1000.0, pmdec=0.0, g=18.0)]))
    assert got and got[0]["values"]["sep_arcsec"] == 1.6


def test_brown_dwarf_like_explained_by_a_catalogued_galaxy():
    got = checks(cand(), ans(xsc=[xsc()], tmass=[tmass()], catwise=[catwise()]))
    assert got["catalogues_checked"]["level"] == "strong" and "galaxy" in got["catalogues_checked"]["text"]


def test_brown_dwarf_like_with_ordinary_star_colours_is_strong_and_l_dwarf_colours_ok():
    star = checks(cand(), ans(tmass=[tmass(j=15.0, h=14.6, k=14.4)], catwise=[catwise(w1=14.3, w2=14.3)]))
    assert star["catalogues_checked"]["level"] == "strong" and "ordinary star" in star["catalogues_checked"]["text"]
    ldw = checks(cand(), ans(tmass=[tmass(j=16.0, h=15.0, k=14.4)], catwise=[catwise(w1=13.9, w2=13.5)]))
    assert ldw["catalogues_checked"]["level"] == "ok" and ldw["catalogues_checked"]["values"]["w1_w2"] == 0.4


def test_gaia_parallax_beyond_50_pc_or_too_luminous_is_strong_and_ultracool_ok():
    def g(plx, err, gm, bp_rp=None):
        return {**gaia(g=gm, bp_rp=bp_rp), "parallax": plx, "parallax_error": err}

    far = checks(cand(), ans(gaia=[g(9.8, 1.1, 20.4)]))["catalogues_checked"]   # 102 pc, below refcat's G 20
    assert far["level"] == "strong" and "beyond 50 pc" in far["text"] and "saw no Gaia star" in far["text"]
    assert far["values"]["distance_pc"] == 102.0
    pah = checks(cand(labels=("pah_33",)), ans(gaia=[g(9.8, 1.1, 20.4)]))["catalogues_checked"]
    assert pah["level"] == "strong"
    assert checks(cand(), ans(gaia=[g(9.8, 3.0, 20.4)]))["catalogues_checked"]["level"] == "warn"  # 3σ: not counted
    ucd = checks(cand(), ans(gaia=[g(41.3, 0.3, 19.2, 3.8)]))["catalogues_checked"]               # 24 pc, M_G 17.3
    assert ucd["level"] == "ok" and ucd["values"]["abs_g"] == 17.28 and "ultracool" in ucd["text"]
    mdw = checks(cand(), ans(gaia=[g(40.0, 0.3, 14.0, 2.5)]))["catalogues_checked"]               # M_G 12.0
    assert mdw["level"] == "strong" and "too luminous" in mdw["text"]
    wd = checks(cand(), ans(gaia=[g(41.3, 0.3, 19.2, 0.5)]))["catalogues_checked"]                # blue: not a UCD
    assert wd["level"] != "ok" or "ultracool" not in wd["text"]


def test_no_j_against_our_46_micron_magnitude():
    own = {"mag_ab_m46": 13.34 + K.AB_OFFSET["w2"], "mag_ab_j": 16.0}  # [4.6] Vega 13.34
    blue = checks(cand(), ans(tmass=[tmass(j=14.0)]), own)["no_j"]
    assert blue["level"] == "strong" and blue["values"]["j_minus_m46"] == 0.66
    red = checks(cand(), ans(tmass=[tmass(j=16.0, h=15.2, k=14.5)]), own)["no_j"]
    assert red["level"] == "ok" and red["values"]["j_minus_m46"] == 2.66
    none = checks(cand(), ans(), own)["no_j"]  # 2MASS saw nothing to J 16.8: J − [4.6] > 3.46
    assert none["level"] == "ok" and none["values"]["j_minus_m46_min"] == 3.46
    shallow = checks(cand(), ans(), {"mag_ab_m46": 15.5 + K.AB_OFFSET["w2"]})["no_j"]
    assert shallow["level"] == "warn"
    # without our own spectrum's 4.6 µm: CatWISE W2 at its place
    cw = checks(cand(), ans(tmass=[tmass(j=13.0)], catwise=[catwise(w2=12.8)]))["no_j"]
    assert cw["level"] == "strong" and cw["values"]["m46_source"] == "CatWISE W2"
    assert "no_j" not in checks(cand(labels=("pah_33",)), ans(tmass=[tmass(j=13.0)]), own)


def test_ice_rule_with_catalogue_colours():
    ok = checks(cand(labels=("h2o_ice",)), ans(tmass=[tmass(j=15.0, h=13.6, k=12.8)], catwise=[catwise(w1=12.0, w2=11.8)]))
    assert ok["catalogues_checked"]["level"] == "ok"
    bare = checks(cand(labels=("h2o_ice",)), ans(tmass=[tmass(j=15.0, h=14.6, k=14.4)]))
    assert bare["catalogues_checked"]["level"] == "strong" and "ice rule" in bare["catalogues_checked"]["text"]
    # reddened, but W1 − W2 0.6 ≥ 0.4: an infrared excess (a young star's envelope holds ice too): warn, not strong
    redw = checks(cand(labels=("h2o_ice",)), ans(tmass=[tmass(j=15.0, h=13.6, k=12.8)], catwise=[catwise(w1=12.0, w2=11.4)]))
    assert redw["catalogues_checked"]["level"] == "warn" and "young star" in redw["catalogues_checked"]["text"]
    assert checks(cand(labels=("h2o_ice",)), ans())["catalogues_checked"]["level"] == "warn"  # nothing catalogued


def test_pah_in_a_star_forming_galaxy_is_ordinary():
    got = checks(cand(labels=("pah_33",)), ans(allwise=[allwise(w2=14.0, w3=11.2, w3snr=8.0, w1=14.2)],
                                                catwise=[catwise(w1=14.2, w2=14.0)]))
    assert got["catalogues_checked"]["level"] == "strong" and "star-forming" in got["catalogues_checked"]["text"]


def test_nothing_catalogued_although_bright_is_a_warning():
    got = checks(cand(flux=5000.0), ans(tmass=[tmass(de=8.0)]))["catalogues_checked"]
    assert got["level"] == "warn" and "No catalogue lists anything within 3″" in got["text"] and "8.0″ east" in got["text"]
    # two CatWISE sources 3.5″ either side: one SPHEREx pixel holds both, so it is likely their blended light
    two = checks(cand(flux=5000.0), ans(catwise=[catwise(de=3.5), catwise(de=-3.5)]))["catalogues_checked"]
    assert two["level"] == "warn" and "blended light" in two["text"] and two["values"]["blend_n"] == 2


def test_spectral_on_a_fast_gaia_star_is_searched_wider_and_catwise_rematched():
    """gaia_match: Gaia is asked within 60″ (the export carries no motion); the CatWISE entry is matched with Gaia's
    motion at CatWISE's own epoch (its own fast motions are often off)."""
    c = cand(gaia_match=True)
    assert K.query_radius(c, "gaia") == K.GAIA_MATCH_ARCSEC and K.query_radius(cand(), "gaia") == K.QUERY_ARCSEC
    yr16 = (MJD - K.GAIA_MJD) / 365.25
    g = {**gaia(de=-1.7 * 1000.0 * yr16 / 1000.0, pmra=1700.0, pmdec=0.0, g=19.0, bp_rp=4.0),
         "parallax": 66.0, "parallax_error": 0.5}                   # 15 pc, 1.7″/yr east: 16″ west in 2016
    yr15 = (MJD - K.CATWISE_PM_MJD) / 365.25
    ra_pm, dec_pm = at(-1.7 * yr15, 0.0)
    cw = {**catwise(de=-1.7 * yr15, w1=13.3, w2=13.1), "ra_pm": ra_pm, "dec_pm": dec_pm,
          "pmra": -1.2, "pmdec": 0.0, "sigpmra": 0.01, "sigpmdec": 0.01}   # CatWISE's own (wrong) motion
    s = K.Spot(c, ans(gaia=[g], catwise=[cw]))
    assert s.gaia is not None and s.at("catwise") is not None and s.at("catwise")["sep"] < 0.1
    got = checks(c, ans(gaia=[g], catwise=[cw]))["catalogues_checked"]
    assert got["level"] == "ok" and "15 pc" in got["text"]


def test_bright_neighbour_from_the_bright_star_query():
    ra, dec = at(0.0, 50.0)
    b = {"designation": "b", "ra": ra, "dec": dec, "j_m": 7.5, "k_m": 7.0, "jdate": 2451300.5}
    got = checks(cand(), ans(tmass_bright=[b]))["bright_neighbour"]
    assert got["level"] == "warn" and "Ks 7.0 star sits 50″ north" in got["text"] and "saturate" in got["text"]
    assert "bright_neighbour" not in checks(cand(), ans(tmass=[tmass(de=20.0, k=14.0)]))  # not much brighter


def test_shape_galaxy():
    s = cand("z-sh-1", kind="shape", labels=())
    assert checks(s, ans(xsc=[xsc(de=6.0)]))["galaxy"]["level"] == "strong"
    assert checks(s, ans(allwise=[allwise(ext="1")]))["galaxy"]["level"] == "warn"
    assert checks(s, ans())["galaxy"]["level"] == "ok"


def test_high_pm_catalogued_along_its_motion_or_still():
    """A 500 mas/yr eastward mover: a Gaia G 14 entry (no motion in DR3) where it was in 2016 → catalogued;
    a 2MASS entry at today's place in 1999 → a still star (its motion would have moved it 13″)."""
    c = cand("z-pm-1", kind="high_pm", labels=(), pm_mas_yr=500.0, pa_deg=90.0)
    back = 0.5 * (MJD - K.GAIA_MJD) / 365.25
    got = K.candidate_checks(c, ans(gaia=[gaia(de=-back, g=14.0)]))
    assert got[0]["level"] == "strong" and "catalogued star" in got[0]["text"] and got[0]["values"]["gaia_without_motion"]
    still = K.candidate_checks(c, ans(tmass=[tmass(de=0.2)]))
    assert still[0]["level"] == "strong" and "still star" in still[0]["text"]
    assert K.candidate_checks(c, ans())[0]["level"] == "warn"
    assert K.candidate_checks({**c, "gaia_match": True}, ans()) is None  # Gaia-matched rows: not measured
    assert abs(K.query_radius(c) - (10.0 + 0.5 * (MJD - K.TMASS_MJD) / 365.25)) < 1e-9 and K.query_radius(cand()) == K.QUERY_ARCSEC


def test_fast_gaia_dwarf_is_searched_where_2mass_saw_it(tmp_path):
    """A brown_dwarf_like source on a 500 mas/yr Gaia star: in 1999 2MASS saw it ~13″ west, outside the 10″ circle.
    The other catalogues are asked out to that distance, and no_j finds its J (not 'nothing to 16.8')."""
    yr16 = (MJD - K.GAIA_MJD) / 365.25
    g = gaia(de=-0.5 * yr16, pmra=500.0, pmdec=0.0, g=19.2, bp_rp=3.8)   # listed at its 2016 place, moving east
    jd = 2451300.5
    back = 0.5 * (MJD - (jd - 2400000.5)) / 365.25
    t = tmass(de=-back, j=14.8, h=14.0, k=13.4, jd=jd)
    c = cand()
    assert K.own_gaia_pm(c, [g]) == 500.0
    r = K.query_radius(c, "tmass", 500.0)
    assert r > back + K.ID_ARCSEC and K.query_radius(c, "gaia", 500.0) == K.QUERY_ARCSEC
    assert K.query_radius(c, "tmass", 20.0) == K.QUERY_ARCSEC and K.query_radius(c, "gaia_bright", 500.0) == K.BRIGHT_ARCSEC
    seen = []

    def query(cat, pos):
        seen.append((cat, pos[0][2]))
        if cat == "gaia":
            return [[g]]
        if cat == "tmass":
            return [[t] if p[2] > back else [] for p in pos]
        yr15 = (MJD - K.CATWISE_PM_MJD) / 365.25  # CatWISE saw it where it was in 2015
        return [[catwise(de=-0.5 * yr15, w2=12.3)] if cat == "catwise" else [] for _ in pos]

    p = tmp_path / "c.json"
    p.write_text(json.dumps({"candidates": [c]}))
    out = K.run(p, None, None, query=query)
    nj = {x["code"]: x for x in out["candidates"]["z-sp-1"]["checks"]}["no_j"]
    assert ("tmass", r) in seen and nj["values"]["j_mag"] == 14.8 and nj["values"]["sep_arcsec"] < 0.5


def test_run_writes_the_producer_file_and_caches_answers(tmp_path):
    doc = {"candidates": [cand(), cand("z-1-1", kind="mover", labels=()),
                          cand("z-pm-2", kind="high_pm", labels=(), gaia_match=True, pm_mas_yr=300.0, pa_deg=0.0)]}
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(doc))
    calls = []

    def query(cat, pos):
        calls.append((cat, len(pos)))
        return [[tmass(de=1.0, j=13.0)] if cat == "tmass" else [] for _ in pos]

    out = K.run(p, tmp_path / "checks" / "catalogue.json", tmp_path / "cache", query=query)
    f = json.loads((tmp_path / "checks" / "catalogue.json").read_text())
    assert f["source"] == "catalogue" and f["candidates_file"] == str(p) and f["made"].endswith("Z")
    assert set(f["candidates"]) == {"z-sp-1", "z-1-1"}  # the Gaia-matched high_pm isn't measured
    e = f["candidates"]["z-sp-1"]
    assert e["status"] is None and e["known_name"] is None
    assert [x["level"] for x in e["checks"]] == sorted((x["level"] for x in e["checks"]), key=K.LEVEL_ORDER.get)
    assert f["candidates"]["z-1-1"]["checks"][0]["code"] == "catalogue_star"
    n = len(calls)
    assert n == len(K.CATALOGUES) and out["candidates"] == f["candidates"]
    K.run(p, tmp_path / "again.json", tmp_path / "cache", query=query)  # every answer cached: no query
    assert len(calls) == n
