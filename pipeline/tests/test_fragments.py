"""fragments: ring fragments, saturated stars and blends from stored tables (synthetic zone), and the consumers' hooks."""

import json

import numpy as np
import pandas as pd
import pytest
from skyshift_core import GAIA_EPOCH_MJD
from skyshift_core.astrometry import DAYS_PER_JYEAR
from skyshift_core.flags import BLOOM, SAT_BITS

from skyshift_pipeline import candidates as K
from skyshift_pipeline import config as C
from skyshift_pipeline import fragments as F
from skyshift_pipeline import shape
from skyshift_pipeline.cli import main
from skyshift_pipeline.sky import offset_radec
from skyshift_pipeline.store import Store, dots_rel, results_rel

ZONE = C.Zone("t_frag", 124.0, 127.0, 18.0, 21.0)
EPOCH = 61000.0
P0, P1, P4, P6, P7, P8, P9 = (125.5, 19.5), (125.0, 19.0), (126.0, 20.0), (124.5, 18.5), (126.5, 18.5), (124.5, 20.5), (126.5, 20.5)
# second sources of one star, stars seen > 3″ off, faint blends (15 Nov)
Q0, Q1, Q2, Q3, Q4, Q5, Q6, Q7 = ((124.3, 19.5), (126.7, 19.5), (125.5, 18.3), (125.5, 20.7), (124.3, 18.3), (126.7, 20.7),
                                  (124.3, 20.7), (126.7, 18.3))
DT_FAST = 180.0  # days between a fast star's two single-visit sources


def off(p, e, n):
    ra, dec = offset_radec(np.array([p[0]]), np.array([p[1]]), np.array([float(e)]), np.array([float(n)]))
    return float(ra[0]), float(dec[0])


def make_zone(tmp_path, flags_peak=True) -> Store:
    store = Store(str(tmp_path))
    dt = (EPOCH - GAIA_EPOCH_MJD) / DAYS_PER_JYEAR
    s3 = off(P1, 0, 20)
    fast_n = 5000.0 * DT_FAST / DAYS_PER_JYEAR / 1000.0  # ″ a 5″/yr star moves between its two sources
    # (source_id, sources.parquet position, link position, BLOOM per member dot, saturated peak per member dot, crossid Gaia)
    src = [
        (1, P0, P0, [1, 1, 1, 1], [1, 1, 1, 0], 1000),       # a G 7 star: saturated_star
        (2, off(P0, 12, 0), None, [1, 1, 1, 0], [0] * 4, -1),  # its ring: bright_fragment, parent 1000 / source 1
        (3, s3, None, [1, 1, 1, 1], [0] * 4, -1),             # 20″ from a G 9 star without a source; a G 11.5 one at 6″
        (4, off(P4, 7, 0), P4, [1, 1, 1, 1], [1] * 4, -1),     # spectrum position 7″ off its G 8 star, link's on it
        (5, off(P4, -10, 0), None, [1, 1, 1, 1], [0] * 4, -1),  # that star's fragment: parent source 4 by position
        (6, P6, None, [0] * 4, [0] * 4, -1),                   # two Gaia stars 2.4″ and 2.5″ away: blend
        (7, P7, None, [1, 1, 1, 1], [0] * 4, -1),              # a fast Gaia star reaches it only at the source's epoch
        (8, P8, None, [0] * 4, [0] * 4, 1007),                 # a plain Gaia star
        (9, off(P9, 0, 30), None, [1, 1, 1, 1], [0] * 4, -1),  # fragment of a 2MASS-only K 5 star
        (10, off(Q0, 0.3, 0), None, [0] * 4, [0] * 4, 1010),    # a G 14 star: crossid's match is kept ...
        (11, off(Q0, -2.6, 0), None, [0] * 6, [0] * 6, -1),     # ... over its second source with more dots: duplicate
        (12, off(Q1, 2.0, 0), None, [0] * 8, [0] * 8, -1),      # no crossid match: the most dots is kept ...
        (13, off(Q1, -2.5, 0), None, [0] * 3, [0] * 3, -1),     # ... the other is its duplicate
        (14, Q2, None, [0] * 4, [0] * 4, 1014),                 # a 5″/yr star's two visits: two sources, no duplicate
        (15, off(Q2, 0, fast_n), None, [0] * 4, [0] * 4, 1014),
        (16, Q3, None, [0] * 5, [0] * 5, 1016),                 # a G 16 star ...
        (17, off(Q3, 2.8, 0), None, [0] * 4, [0] * 4, -1),      # ... and a CatWISE object 0.2″ from this one: not its duplicate
        (18, off(Q4, -0.2, 0), None, [0] * 6, [0] * 6, 1018),   # a G 17 star ...
        (19, off(Q4, 1.4, 0), None, [0] * 4, [0] * 4, -1),      # ... and a source that may be a G 19.5 star without one
        (20, off(Q5, 3.6, 0), None, [1] * 6, [1] * 6, -1),      # a G 10.5 star seen 3.6″ off (BLOOM): the star ...
        (21, off(Q5, -12, 0), None, [1] * 4, [0] * 4, -1),      # ... its ring: parent source 20
        (22, off(Q6, 3.5, 0), None, [1] * 4, [0] * 4, -1),      # a 4-dot BLOOM piece 3.5″ from a G 15 star: a fragment
        (23, off(Q7, 3.5, 0), None, [1] * 12, [0] * 12, -1),    # 12 BLOOM dots 3.5″ from a G 15 star: that star
        (24, off(P1, -30, 30), None, [0] * 4, [0] * 4, -1),     # between two G 18.4 / 18.6 stars: blend (G < 19)
    ]
    epochs = {15: EPOCH + DT_FAST}
    gaia = [  # (id, position at J2016, G, pmdec mas/yr)
        (1000, P0, 7.0, 0.0), (1001, P1, 9.0, 0.0), (1002, off(s3, 6, 0), 11.5, 0.0), (1003, P4, 8.0, 0.0),
        (1004, off(P6, 2.5, 0), 14.0, 0.0), (1005, off(P6, -2.4, 0), 15.5, 0.0),
        (1006, off(P7, 1.0, -2000.0 * dt / 1000.0), 16.0, 2000.0), (1007, off(P8, 0.5, 0), 13.0, 0.0),
        (1010, Q0, 14.0, 0.0), (1012, Q1, 15.0, 0.0), (1014, off(Q2, 0, -5000.0 * dt / 1000.0), 13.0, 5000.0),
        (1016, Q3, 16.0, 0.0), (1018, Q4, 17.0, 0.0), (1019, off(Q4, 3.2, 0), 19.5, 0.0), (1020, Q5, 10.5, 0.0),
        (1022, Q6, 15.0, 0.0), (1023, Q7, 15.0, 0.0), (1024, off(P1, -32.5, 30), 18.4, 0.0),
        (1025, off(P1, -27.6, 30), 18.6, 0.0)]
    cw = off(Q3, 3.0, 0)
    ref = pd.DataFrame({
        "ref_id": [f"gaia:{g[0]}" for g in gaia] + ["2mass:J1", "catwise:W1"],
        "cat": ["gaia"] * len(gaia) + ["2mass", "catwise"],
        "ra": [g[1][0] for g in gaia] + [P9[0], cw[0]], "dec": [g[1][1] for g in gaia] + [P9[1], cw[1]],
        "epoch_mjd": [GAIA_EPOCH_MJD] * len(gaia) + [np.nan] * 2, "pmra": 0.0,
        "pmdec": [g[3] for g in gaia] + [np.nan] * 2, "parallax": np.nan, "g": [g[2] for g in gaia] + [np.nan] * 2,
        "k": [np.nan] * len(gaia) + [5.0, np.nan], "w1": [np.nan] * len(gaia) + [np.nan, 15.0]})
    store.write_parquet(C.REFCAT_REL.format(zone=ZONE.name), ref)
    dots, members = {}, []
    for sid, pos, _, bloom, sat, _ in src:
        for k in range(len(bloom)):
            d = dots.setdefault(("A" if k < 2 else "B", 1), [])
            members.append((sid, "A" if k < 2 else "B", 1, len(d)))
            d.append({"ra": pos[0], "dec": pos[1], "flags": BLOOM * bloom[k] | (1 << 20), "flags_peak": SAT_BITS * sat[k]})
    for (obs, det), d in dots.items():
        t = pd.DataFrame(d)
        store.write_parquet(dots_rel(obs, det), t if flags_peak else t.drop(columns="flags_peak"))
    store.write_parquet(results_rel(ZONE.name, "members.parquet"),
                        pd.DataFrame(members, columns=["source_id", "obs_id", "detector", "dot_row"]))
    ids = [s[0] for s in src]
    store.write_parquet(results_rel(ZONE.name, "sources.parquet"),
                        pd.DataFrame({"source_id": ids, "ra": [s[1][0] for s in src], "dec": [s[1][1] for s in src]}))
    store.write_parquet(results_rel(ZONE.name, "fixed.parquet"), pd.DataFrame({
        "source_id": ids, "ra": [(s[2] or s[1])[0] for s in src], "dec": [(s[2] or s[1])[1] for s in src]}))
    store.write_parquet(results_rel(ZONE.name, "crossids.parquet"), pd.DataFrame({
        "source_id": ids, "epoch_mjd": [epochs.get(i, EPOCH) for i in ids], "gaia_id": [s[5] for s in src],
        "gaia_sep_arcsec": [0.0 if s[5] >= 0 else np.nan for s in src]}))
    return store


def test_fragments_step_tags_fragments_saturated_stars_and_blends(tmp_path, monkeypatch):
    store = make_zone(tmp_path)
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    assert main(["fragments", "--zone", ZONE.name, "--root", str(tmp_path), "--workers", "2"]) == 0
    t = store.read_parquet(results_rel(ZONE.name, F.FILE))
    assert list(t.columns) == F.COLUMNS
    t = t.set_index("source_id")
    assert t["tag"].to_dict() == {1: "saturated_star", 2: "bright_fragment", 3: "bright_fragment", 4: "saturated_star",
                                  5: "bright_fragment", 6: "blend", 7: "saturated_star", 8: "", 9: "bright_fragment",
                                  10: "", 11: "duplicate", 12: "", 13: "duplicate", 14: "", 15: "", 16: "", 17: "", 18: "",
                                  19: "", 20: "saturated_star", 21: "bright_fragment", 22: "bright_fragment",
                                  23: "saturated_star", 24: "blend"}
    assert t.loc[1, "n_member_dots"] == 4 and t.loc[2, "bloom_frac"] == pytest.approx(0.75)
    assert t.loc[1, "sat_frac"] == pytest.approx(0.75) and t.loc[8, "transient_frac"] == 0.0
    # Gaia moved by its proper motion to the source's epoch: 1″, not the catalogue's 19.8″
    assert t.loc[7, "cat_dist_arcsec"] == pytest.approx(1.0, abs=0.01)
    # link's position finds the saturated star its spectrum position missed by 7″
    assert t.loc[4, "match_dist_arcsec"] == pytest.approx(0.0, abs=0.01)
    assert t.loc[8, "cat_dist_arcsec"] == pytest.approx(0.5, abs=0.01)
    # parents: the brightest Gaia star within 60″ (not the nearer G 11.5), its own source by crossid or by position
    assert t.loc[2, ["parent_gaia_id", "parent_source_id"]].tolist() == [1000, 1]
    assert t.loc[2, "parent_mag"] == pytest.approx(7.0) and t.loc[2, "parent_dist_arcsec"] == pytest.approx(12.0, abs=0.01)
    assert t.loc[3, ["parent_gaia_id", "parent_source_id"]].tolist() == [1001, -1]
    assert t.loc[3, "cat_dist_arcsec"] == pytest.approx(6.0, abs=0.01)
    assert t.loc[5, ["parent_gaia_id", "parent_source_id"]].tolist() == [1003, 4]
    assert t.loc[9, ["parent_gaia_id", "parent_source_id"]].tolist() == [-1, -1]
    assert t.loc[9, "bright_band"] == "K" and t.loc[9, "bright_dist_arcsec"] == pytest.approx(30.0, abs=0.01)
    assert t.loc[3, "bright_band"] == "G" and t.loc[3, "bright_ref_id"] == "gaia:1001"
    assert (t.loc[[1, 4, 6, 7, 8], "parent_gaia_id"] == -1).all() and t.loc[[1, 6], "parent_mag"].isna().all()
    # the blend: both Gaia ids nearest first, their separation and ΔG
    assert t.loc[6, "n_gaia_8"] == 2 and list(t.loc[6, "blend_ids"]) == [1005, 1004]
    assert t.loc[6, "blend_sep_arcsec"] == pytest.approx(4.9, abs=0.01) and t.loc[6, "blend_dg"] == pytest.approx(1.5)
    assert list(t.loc[8, "blend_ids"]) == [] and np.isnan(t.loc[8, "blend_sep_arcsec"])
    # one star, two sources: the duplicate names the kept source (crossid's match, else the most dots) and the star
    assert t.loc[11, ["duplicate_of", "parent_source_id", "parent_gaia_id"]].tolist() == [10, 10, 1010]
    assert t.loc[11, "parent_dist_arcsec"] == pytest.approx(2.6, abs=0.01) and t.loc[11, "parent_mag"] == pytest.approx(14.0)
    assert t.loc[13, ["duplicate_of", "parent_gaia_id"]].tolist() == [12, 1012]
    assert (t.loc[[10, 12, 14, 15, 16, 17, 18, 19, 20], "duplicate_of"] == -1).all()
    # a star seen 3.6″ off is that star (G < 11), and its ring's parent source; a faint star's needs FRAGMENTS_STAR_MIN_DOTS
    assert t.loc[20, "match_dist_arcsec"] == pytest.approx(3.6, abs=0.01) and t.loc[20, "parent_gaia_id"] == -1
    assert t.loc[21, ["parent_gaia_id", "parent_source_id"]].tolist() == [1020, 20]
    # blends count Gaia companions to G 19
    assert list(t.loc[24, "blend_ids"]) == [1025, 1024] and t.loc[24, "blend_dg"] == pytest.approx(0.2, abs=1e-4)
    rep = json.loads((tmp_path / f"results/{ZONE.name}/{F.REPORT}").read_text())
    assert rep["counts"]["tags"] == {"bright_fragment": 6, "duplicate": 2, "saturated_star": 5, "blend": 2}
    assert rep["bright_fragment_parents"]["with_parent"] == 4 and rep["bright_fragment_parents"]["with_parent_source"] == 3
    assert rep["duplicates"]["stars"] == 2 and rep["stats"]["stars"]["not_duplicate"] == {
        "star_moved": 1, "own_catalogue_entry": 1, "other_star_without_source": 1}
    assert "seconds" not in rep["stats"]
    summ = json.loads((tmp_path / f"results/{ZONE.name}/summary.json").read_text())["steps"]["fragments"]
    assert summ["tags"]["blend"] == 2 and summ["tags"]["duplicate"] == 2 and "error" not in summ


def test_every_distance_takes_the_nearer_of_spectrum_and_link_positions():
    """bright_*, the blend count and a fragment's parent are measured from link's position too (as cat/match_dist), the
    smaller wins; a source whose two positions agree keeps its spectrum position's values."""
    star, b0, b1 = (125.0, 19.0), (125.5, 19.5), (126.0, 20.0)
    ref = pd.DataFrame({
        "ref_id": ["gaia:1", "gaia:2", "gaia:3", "gaia:4", "2mass:K"], "cat": ["gaia"] * 4 + ["tmass"],
        "ra": [star[0], off(b0, -5, 0)[0], off(b0, 12, 0)[0], b1[0], off(b1, 0, 40)[0]],
        "dec": [star[1], off(b0, -5, 0)[1], off(b0, 12, 0)[1], b1[1], off(b1, 0, 40)[1]],
        "epoch_mjd": [GAIA_EPOCH_MJD] * 4 + [np.nan], "pmra": 0.0, "pmdec": [0.0] * 4 + [np.nan],
        "g": [8.0, 17.0, 17.5, 13.0, np.nan], "k": [np.nan] * 4 + [6.0], "w1": np.nan})
    # 0: a ring piece 20″ east of the G 8 star, link's position 12″ east; 1: 5″ / 12″ from two faint stars, link's 11″ / 6″;
    # 2: on a G 13 star, both positions agree (0.2″), a K 6 2MASS star 40″ north
    pos = [off(star, 20, 0), b0, b1]
    lpos = [off(star, 12, 0), off(b0, 6, 0), off(b1, 0.2, 0)]
    ra, dec = np.array([p[0] for p in pos]), np.array([p[1] for p in pos])
    lra, ldec = np.array([p[0] for p in lpos]), np.array([p[1] for p in lpos])
    mjd = np.full(3, EPOCH)
    cat, st = F.catalogue_columns(ra, dec, mjd, ref, (lra, ldec))
    assert st["link_position_also"] == 2
    assert cat.loc[0, "bright_ref_id"] == "gaia:1" and cat.loc[0, "bright_dist_arcsec"] == pytest.approx(12.0, abs=0.01)
    assert cat.loc[0, "cat_dist_arcsec"] == pytest.approx(12.0, abs=0.01)
    assert cat.loc[1, "n_gaia_8"] == 2 and list(cat.loc[1, "blend_ids"]) == [2, 3]   # 5″ (spectrum) and 6″ (link), nearest first
    assert cat.loc[1, "blend_sep_arcsec"] == pytest.approx(17.0, abs=0.01)          # the two stars' own separation
    assert cat.loc[2, "n_gaia_8"] == 1 and cat.loc[2, "cat_dist_arcsec"] == pytest.approx(0.0, abs=0.01)
    assert cat.loc[2, "bright_band"] == "K" and cat.loc[2, "bright_dist_arcsec"] == pytest.approx(40.0, abs=0.01)
    spec_only, _ = F.catalogue_columns(ra, dec, mjd, ref, None)
    assert spec_only.loc[0, "bright_dist_arcsec"] == pytest.approx(20.0, abs=0.01) and spec_only.loc[1, "n_gaia_8"] == 1
    par, _ = F.parents(ra[:1], dec[:1], mjd[:1], ref, np.array([7, 8, 9]), [(ra, dec), (lra, ldec)], None, (lra[:1], ldec[:1]))
    assert par.loc[0, "parent_gaia_id"] == 1 and par.loc[0, "parent_dist_arcsec"] == pytest.approx(12.0, abs=0.01)
    par, _ = F.parents(ra[:1], dec[:1], mjd[:1], ref, np.array([7, 8, 9]), [(ra, dec)], None)
    assert par.loc[0, "parent_dist_arcsec"] == pytest.approx(20.0, abs=0.01)


def test_without_flags_peak_sat_frac_is_nan(tmp_path):
    store = make_zone(tmp_path, flags_peak=False)
    shares, st = F.member_flag_shares(store, ZONE, np.array([1, 2, 99]), workers=2)
    assert st["files_without_flags_peak"] == 2 and shares["sat_frac"].isna().all()
    assert shares["n_member_dots"].tolist() == [4, 4, 0] and np.isnan(shares.loc[2, "bloom_frac"])


def test_the_step_never_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    assert main(["fragments", "--zone", ZONE.name, "--root", str(tmp_path)]) == 0  # no tables at all
    summ = json.loads((tmp_path / f"results/{ZONE.name}/summary.json").read_text())["steps"]["fragments"]
    assert "error" in summ
    assert F.load(Store(str(tmp_path)), ZONE) is None


def write_tags(store: Store, zone: str, tags: dict[int, str]) -> None:
    store.write_parquet(results_rel(zone, F.FILE), pd.DataFrame({"source_id": list(tags), "tag": list(tags.values())}))


def test_load_and_tag_of(tmp_path):
    store = Store(str(tmp_path))
    assert F.load(store, "hunt") is None and F.tag_of(None, [1, 2]).tolist() == ["", ""]
    write_tags(store, "hunt", {1: "bright_fragment", 2: "", 3: "blend"})
    tg = F.load(store, "hunt")
    assert tg.to_dict() == {1: "bright_fragment", 3: "blend"}
    assert F.tag_of(tg, [3, 2, 1, 7]).tolist() == ["blend", "", "bright_fragment", ""]


def test_highpm_hook():
    c = pd.DataFrame({"method": ["fit", "pair", "fit"], "status": ["new", "new", "new"], "source_id": [2, 8, 9],
                      "source_id_b": [-1, 6, -1], "reason": ["", "", "short_span"], "vetted": [True, True, False],
                      "score": [0.1, 0.2, 0.3]})
    same, st = F.apply_highpm(c, None)
    assert same is c and st["applied"] is False
    out, st = F.apply_highpm(c, pd.Series({2: "bright_fragment", 6: "blend"}))
    assert st == {"applied": True, "fragment": 1, "blend_not_new": 1}
    assert out["source_id"].tolist() == [8, 2, 9]                      # vetted first
    assert out["reason"].tolist() == ["", "fragment", "short_span"] and out["vetted"].tolist() == [True, False, False]
    assert out["status"].tolist() == ["gaia", "new", "new"]
    out, st = F.apply_highpm(c, pd.Series({9: "duplicate", 6: "blend"}))  # a star's second source: as a fragment
    assert st == {"applied": True, "fragment": 1, "blend_not_new": 1}
    assert out.set_index("source_id").loc[9, "reason"] == "short_span,fragment"


def test_candidates_hook_unit():
    cands = [{"id": "a", "reasons": ["pm_fit"], "_sources": [1, 2]}, {"id": "b", "reasons": ["no_gaia"], "_sources": [3]},
             {"id": "c", "reasons": ["two_exposures"]}]
    same, st = F.apply_candidates(cands, None)
    assert same is cands and st["applied"] is False
    out, st = F.apply_candidates([dict(c, reasons=list(c["reasons"])) for c in cands], pd.Series({2: "bright_fragment", 3: "blend"}))
    assert [c["id"] for c in out] == ["b", "c"] and out[0]["reasons"] == ["no_gaia", "blend"]
    assert st == {"applied": True, "dropped_fragment": 1, "blend": 1}
    out, st = F.apply_candidates([dict(c, reasons=list(c["reasons"])) for c in cands], pd.Series({3: "duplicate"}))
    assert [c["id"] for c in out] == ["a", "c"] and st["dropped_fragment"] == 1


def test_candidates_build_uses_the_tags(tmp_path):
    from test_candidates import _zone_files
    store = Store(str(tmp_path / "bucket"))
    _zone_files(store, "hunt", 125.0, 20.0, with_extra_mover=False)
    write_tags(store, "hunt", {100: "blend", 101: "duplicate"})
    doc = K.build(store, ["hunt"], tmp_path / "data")
    ids = [c["id"] for c in doc["candidates"]]
    assert "hunt-sp-101" not in ids and "hunt-sp-100" in ids and doc["counts"]["spectral"] == 1
    for c in doc["candidates"]:
        assert set(c["reasons"]) <= set(K.REASONS[c["kind"]])
        if c["kind"] in ("high_pm", "spectral"):
            assert "blend" in c["reasons"]


def test_orphans_drop_fragments_and_blends(tmp_path, rng, monkeypatch):
    from test_orphans import ZONE as OZ
    from test_orphans import make_zone as orphan_zone
    store = orphan_zone(tmp_path, rng)
    monkeypatch.setitem(C.ZONES, OZ.name, OZ)
    monkeypatch.setattr(C, "ORPHANS_CONTROL_N", 2)
    write_tags(store, OZ.name, {101: "bright_fragment", 201: "blend", 206: "duplicate"})
    assert main(["orphans", "--zone", OZ.name, "--root", str(tmp_path), "--workers", "2"]) == 0
    o = store.read_parquet(results_rel(OZ.name, "orphans.parquet")).set_index("source_id")
    assert o.loc[101, "reason"] == "bright_fragment" and o.loc[201, "reason"] == "blend"
    assert o.loc[206, "reason"] == "bright_fragment"  # a star's second source counts as a piece of it
    assert o.loc[204, "reason"] == "bright_fragment" and o.loc[102, "reason"] == ""   # its own rule still applies
    rep = json.loads((tmp_path / f"results/{OZ.name}/distant.json").read_text())
    assert rep["pairs"] == []                       # the planet's visit-1 half is gone


def test_shape_select_excludes_fragments(monkeypatch):
    t = pd.DataFrame({"source_id": [1, 2, 3], "sig": [30.0, 20.0, 10.0], "class": "star", "galaxy_or_qso": False})
    yes = lambda t: np.ones(len(t), bool)  # noqa: E731
    for f in ("fires", "one_object", "clean_sources"):
        monkeypatch.setattr(shape, f, yes)
    c, info = shape.select(t, cap=10)
    assert c["source_id"].tolist() == [1, 2, 3] and info["fragment_tags"] is False
    c, info = shape.select(t, cap=10, exclude=np.array([1], np.int64))
    assert c["source_id"].tolist() == [2, 3] and info["fragments_dropped"] == 1 and info["fragment_tags"] is True
