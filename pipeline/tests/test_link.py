import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.link import classify, pair_candidates
from skyshift_pipeline.sky import Footprint, offset_radec

from conftest import t
from test_photolist import square

STAR = (122.30, 16.10)


class Scene:
    """Synthetic in-zone dots in link's load format, with N sessions of 2 exposures, all covering
    the field (photos list with footprints and sessions)."""

    def __init__(self, n_sessions=5, n_exp=2):
        self.rows = []
        self.photos = []
        self.n_exp = n_exp
        for s in range(n_sessions):
            for e in range(n_exp):
                for det in (1, 4):
                    self.photos.append(dict(obs_id=f"S{s}E{e}", detector=det, mjd=t(s, e), session=s + 1, visit=1,
                                            s_region=square(122.3, 16.15)))

    def add(self, ra, dec, s, e, det=1, snr=20.0, elong=1.1, flags=0, flags_peak=0, flux=100.0):
        self.rows.append(dict(detector=det, mjd=t(s, e), ra=ra, dec=dec, flux_ujy=flux, snr=snr, flags=flags,
                              flags_peak=flags_peak, elong=elong, session=s + 1, visit=1, photo=0, exposure=s * 10 + e))
        return self

    def star(self, ra, dec, sessions=range(5)):
        for s in sessions:
            for e in range(self.n_exp):
                self.add(ra, dec, s, e, snr=200)
        return self

    def track(self, ra, dec, s, step_east, exposures, **kw):
        """An asteroid moving step_east arcsec per exposure (2.1 min) in the given exposures of session s."""
        for e in exposures:
            self.add(*offset_radec(ra, dec, step_east * e, 0.0), s, e, **kw)
        return self

    def run(self, photos=None):
        p = pd.DataFrame(photos if photos is not None else self.photos)
        return classify(pd.DataFrame(self.rows), p)


def test_star_is_fixed_and_vetoes_only_nearby():
    sc = Scene().star(*STAR)
    fixed, movers, st = sc.run()
    assert len(fixed) == 1 and fixed["n_sessions"].iloc[0] == 5 and len(movers) == 0


def test_asteroid_two_exposures_next_to_a_star():
    sc = Scene().star(*STAR)
    # asteroid 7″ from the star in session 2, moving 1.5″ between exposures 2.1 min apart
    a0 = offset_radec(*STAR, 7.0, 0.0)
    a1 = offset_radec(*STAR, 8.5, 0.0)
    sc.add(*a0, 2, 0, snr=12).add(*a1, 2, 1, snr=11)
    fixed, movers, st = sc.run()
    assert len(fixed) == 1
    assert len(movers) == 1, st
    m = movers.iloc[0]
    assert m["evidence"] == "two_exposures" and m["n_dots"] == 2 and m["snr"] == 12 and m["session"] == 3
    assert m["cover_sessions"] == 5


def test_asteroid_within_veto_radius_is_lost():
    sc = Scene().star(*STAR)
    sc.add(*offset_radec(*STAR, 5.0, 0.0), 2, 0).add(*offset_radec(*STAR, 5.5, 0.0), 2, 1)
    _, movers, _ = sc.run()
    assert len(movers) == 0  # within FIXED_VETO_ARCSEC of the star
    assert C.FIXED_VETO_ARCSEC == 6.0


def test_stray_dot_elsewhere_does_not_veto():
    pos = (122.0, 16.0)
    for det, flux in ((4, 100.0), (1, 900.0)):  # a stray dot at the same place in another session: another detector,
        sc = Scene()                             # or 9× brighter in its detector
        sc.add(*pos, 2, 0).add(*offset_radec(*pos, 1.0, 0.0), 2, 1)
        sc.add(*pos, 4, 0, det=det, flux=flux)
        _, movers, st = sc.run()
        assert len(movers) == 1 and st["fixed"] == 0 and not movers["still_free"].iloc[0]


def test_still_free_star_is_not_a_mover():
    """A star seen in < 3 sessions is never fixed: its dots pair inside a session (scatter) and the same spot shows a free
    dot in other sessions (B4: 190 of 372 unmatched box candidates vs 3 of 101 known asteroids)."""
    from skyshift_pipeline.link import still_free
    pos = (122.0, 16.0)
    for flux in (100.0, 150.0):  # one other session, in its detector, within 2× in flux
        sc = Scene()
        sc.add(*pos, 2, 0).add(*offset_radec(*pos, 1.0, 0.0), 2, 1)
        sc.add(*pos, 4, 0, flux=flux)
        _, movers, st = sc.run()
        assert len(movers) == 0 and st["vetoed_still"] == 1, flux
    # free dots of two other sessions (any detector, any flux); in classify such dots would mostly be a fixed group
    mv = pd.DataFrame(dict(ra=[pos[0]], dec=[pos[1]], session=[3], dot_det=[[1, 1]], dot_flux=[[100.0, 100.0]]))
    free = pd.DataFrame(dict(ra=[pos[0]] * 3, dec=[pos[1]] * 3, session=[3, 1, 5], detector=[1, 4, 4], flux_ujy=[100.0, 5.0, 5.0]))
    assert still_free(mv, free).tolist() == [True] and still_free(mv, free.iloc[:2]).tolist() == [False]


def test_glitch_and_too_fast_and_trail():
    sc = Scene()
    sc.add(122.0, 16.0, 1, 0)                                                 # single-photo glitch
    sc.add(122.1, 16.0, 1, 0).add(*offset_radec(122.1, 16.0, 30.0, 0.0), 1, 1)  # 30″ in 2.1 min: too far
    sc.add(122.2, 16.0, 1, 0, elong=5).add(122.2, 16.0, 1, 1, elong=5)        # elongated: trails/cosmic rays
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["candidates"] == 0


def test_simultaneous_detector_pair():
    sc = Scene()
    sc.add(122.0, 16.0, 3, 0, det=1, snr=9).add(*offset_radec(122.0, 16.0, 1.0, 0.0), 3, 0, det=4, snr=8)
    sc.add(122.2, 16.2, 3, 0, det=1).add(122.2, 16.2, 3, 0, det=2)  # D1+D2 is not a simultaneous pair
    # session 4's second exposure points elsewhere: the pair is the only look at that spot
    photos = [dict(p, s_region=square(140.0, 16.15)) if p["obs_id"] == "S3E1" else p for p in sc.photos]
    _, movers, st = sc.run(photos)
    assert len(movers) == 1, st
    m = movers.iloc[0]
    assert m["evidence"] == "two_detectors" and m["detectors"] == "1,4" and m["det"] == 1
    assert m["cover_exposures"] == 1 and m["missed_exposures"] == 0


def test_simultaneous_pair_missing_from_a_covering_exposure_is_dropped():
    sc = Scene()
    sc.add(122.0, 16.0, 3, 0, det=1, snr=9).add(*offset_radec(122.0, 16.0, 1.0, 0.0), 3, 0, det=4, snr=8)
    _, movers, st = sc.run()  # exposure S3E1 covers the spot 2.1 min later and shows nothing
    assert len(movers) == 0 and st["vetoed_missed"] == 1


def test_two_exposures_out_of_four_is_dropped_all_four_kept():
    sc = Scene(n_exp=4)
    sc.track(122.0, 16.0, 2, 1.4, [0, 1])            # seen in 2 of the 4 covering exposures: chance pair
    sc.track(122.1, 16.0, 2, 1.4, [0, 1, 2, 3])      # a real asteroid: 40″/h, every exposure
    sc.track(122.2, 16.0, 2, 1.4, [0, 1, 2])         # 3 of 4: one miss allowed (masked pixel, faint)
    _, movers, st = sc.run()
    assert sorted(movers["n_exposures"].tolist()) == [3, 4] and st["vetoed_missed"] == 1
    m = movers[movers["n_exposures"] == 4].iloc[0]
    assert m["rate_arcsec_h"] == pytest.approx(1.4 / 2.1 * 60, rel=1e-3) and m["pa_deg"] == pytest.approx(90.0, abs=0.1)
    assert m["line_resid_arcsec"] < 0.01 and m["cover_exposures"] == 4 and m["missed_exposures"] == 0


def test_flux_jump_is_dropped():
    sc = Scene(n_exp=3)
    b = (122.1, 16.0)
    sc.track(*b, 1, 1.0, [0, 1], flux=100.0).add(*offset_radec(*b, 2.0, 0.0), 1, 2, flux=500.0)  # x5 in one detector
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["vetoed_flux"] == 1


def test_track_fit_and_vet_rules():
    from skyshift_pipeline.link import track_fit, vet_reason
    ra = 122.0 + np.array([0.0, 2.0, 4.0, 6.0]) / 3600 / np.cos(np.deg2rad(16.0))
    dec = 16.0 + np.array([0.0, 0.0, 6.0, 0.0]) / 3600  # the third exposure 6″ off: 4.2″ from the fitted line
    tr = track_fit(ra, dec, t(0, 0) + np.arange(4) * 2.1 / 1440, [0, 1, 2, 3], [1, 1, 1, 1], [100, 110, 90, 100])
    assert tr["line_resid_arcsec"] == pytest.approx(4.2, abs=0.05) and tr["flux_ratio"] == pytest.approx(110 / 90)
    assert tr["dup_dots"] == 0 and tr["rate_arcsec_h"] == pytest.approx(np.hypot(2.0, 0.6) / 2.1 * 60, rel=0.01)
    m = pd.DataFrame(dict(n_exposures=[4, 2, 3, 2, 2, 3, 2], line_resid_arcsec=[4.5, np.nan, 1.0, np.nan, np.nan, 1.0, np.nan],
                          dup_dots=[0, 0, 1, 0, 0, 0, 0], flux_ratio=[1.2, 3.5, 1.0, 1.0, 1.0, 1.0, 1.0],
                          missed_exposures=[0, 0, 0, 1, 0, 1, 0], edge_exposures=[0, 0, 0, 0, 1, 0, 0]))
    assert vet_reason(m).tolist() == ["line", "flux", "dup", "missed", "edge", "", ""]


def test_edge_dots_do_not_make_movers():
    sc = Scene()
    edge = square(122.3, 16.15)  # the field is 122.3 ± 1.75° / cos(dec) in RA
    ra_edge = 122.3 + 1.75 / np.cos(np.deg2rad(16.15)) - 3 * C.PIXEL_ARCSEC / 3600 / np.cos(np.deg2rad(16.15))
    sc.track(ra_edge, 16.15, 2, 1.0, [0, 1])  # 3 px inside the edge in both exposures
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["vetoed_edge"] == 1 and edge


def test_fast_mover_confirmed_by_detector_pairs():
    sc = Scene(n_exp=3)
    a = (122.0, 16.0)
    for e in range(3):  # 300″/h: 10.5″ per 2.1 min, beyond 3″ + 1″/min, each exposure a D1+D4 pair
        p = offset_radec(*a, 10.5 * e, 0.0)
        sc.add(*p, 2, e, det=1).add(*p, 2, e, det=4)
    _, movers, st = sc.run()
    assert len(movers) == 1, st
    assert movers["n_exposures"].iloc[0] == 3 and movers["rate_arcsec_h"].iloc[0] == pytest.approx(300.0, rel=1e-3)


def test_asteroid_chained_into_a_star_is_released(monkeypatch):
    sc = Scene(n_exp=4).star(*STAR)
    # 100″/h through the star: 3.5″ per exposure, so the 4″ FoF chains the whole track into the star's group
    sc.track(*offset_radec(*STAR, 2.0, 0.0), 2, 3.5, [0, 1, 2, 3])
    fixed, movers, st = sc.run()
    assert len(fixed) == 1 and fixed["n_sessions"].iloc[0] == 5
    assert len(movers) == 1, st  # the two dots beyond 6″ (9″, 12.5″) pair; the two near the star are vetoed
    m = movers.iloc[0]
    assert m["n_exposures"] == 2 and m["missed_exposures"] == 0  # exposures where it sat on the star don't count
    monkeypatch.setattr(C, "FIXED_RELEASE_ARCSEC", None)
    _, movers, _ = sc.run()
    assert len(movers) == 0  # the old behaviour: swallowed by the star


def test_under_covered_position():
    sc = Scene(n_sessions=2)
    sc.add(122.0, 16.0, 1, 0).add(*offset_radec(122.0, 16.0, 1.0, 0.0), 1, 1)
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["dropped_under_covered"] == 1


def test_pair_rule_scales_with_time():
    d = pd.DataFrame(dict(ra=[122.0, offset_radec(122.0, 16.0, 12.0, 0.0)[0]], dec=[16.0, 16.0],
                          mjd=[61000.0, 61000.0 + 10 / 1440], exposure=[0, 1], detector=[1, 1]))
    pairs, kind = pair_candidates(d)  # 12″ in 10 min <= 3 + 1×10 = 13″
    assert len(pairs) == 1 and kind[0] == 0
    d.loc[1, "mjd"] = 61000.0 + 5 / 1440  # 12″ in 5 min > 8″
    assert len(pair_candidates(d)[0]) == 0


def test_source_bit_ignored_in_photos_where_it_is_everywhere(tmp_path):
    from skyshift_pipeline.dots import DOT_COLUMNS
    from skyshift_pipeline.link import load_dots
    from skyshift_pipeline.store import Store, dots_rel

    store = Store(str(tmp_path))
    photos = pd.DataFrame([dict(obs_id="A", detector=1, session=1, visit=1), dict(obs_id="B", detector=1, session=2, visit=1)])
    for o, frac in (("A", 1.0), ("B", 0.5)):
        n = 10
        fp = np.where(np.arange(n) < frac * n, C.SOURCE_BIT, 0)
        df = pd.DataFrame({c: np.zeros(n) for c in DOT_COLUMNS})
        df["ra"], df["dec"], df["flags_peak"], df["obs_id"] = 122.0, 16.0, fp, o
        store.write_parquet(dots_rel(o, 1), df.astype(DOT_COLUMNS))
    d, missing = load_dots(store, photos, C.ZONES["pilot"])
    assert missing == 0
    a = d[d["photo"] == 0]["flags_peak"].to_numpy()
    b = d[d["photo"] == 1]["flags_peak"].to_numpy()
    assert not (a & C.SOURCE_BIT).any() and ((b & C.SOURCE_BIT) != 0).sum() == 5
    assert {"x", "y", "size", "psf_chi2_nu"} <= set(d.columns)  # carried for the suspects' gates


def test_movers_carry_their_dots_for_vetting():
    """Each mover lists its dots in time order (dot_obs names, detector, pixel, flux, S/N, size, χ², flags at the peak);
    columns a scene lacks come out NaN."""
    sc = Scene()
    sc.add(*offset_radec(122.0, 16.0, 2.0, 0.0), 3, 1, snr=8, flags_peak=1 << 19).add(122.0, 16.0, 3, 0, snr=9)
    sc.rows[0].update(x=10.0, y=20.0, size=4.5, psf_chi2_nu=2.0)
    sc.rows[1].update(x=11.0, y=135.0, size=4.6, psf_chi2_nu=2.5)
    p = pd.DataFrame(sc.photos)
    _, movers, _ = classify(pd.DataFrame(sc.rows), p, exposure_names=[f"X{i}" for i in range(40)])
    m = movers.iloc[0]
    assert list(m["dot_obs"]) == ["X30", "X31"] and list(m["dot_det"]) == [1, 1]
    assert list(m["dot_x"]) == [11.0, 10.0] and list(m["dot_y"]) == [135.0, 20.0] and list(m["dot_snr"]) == [9.0, 8.0]
    assert list(m["dot_fpk"]) == [0, 1 << 19] and list(m["dot_size"]) == [4.6, 4.5] and list(m["dot_row"]) == [-1, -1]
    sc2 = Scene()
    sc2.add(122.0, 16.0, 3, 0).add(*offset_radec(122.0, 16.0, 1.0, 0.0), 3, 1)
    m2 = sc2.run()[1].iloc[0]
    assert np.isnan(m2["dot_x"]).all() and len(m2["dot_obs"]) == 2


def test_bright_asteroid_chained_into_a_fainter_star(monkeypatch):
    """B1 (Alekto s75): an asteroid >= 3× a star's flux chained into its group (2.5″, 4″ off) is released and escapes the
    6″ veto; the star keeps its dots."""
    sc = Scene().star(*STAR)
    sc.track(*offset_radec(*STAR, 2.5, 0.0), 2, 1.5, [0, 1], flux=600.0, snr=30.0)
    fixed, movers, st = sc.run()
    assert len(fixed) == 1 and fixed["n_sessions"].iloc[0] == 5 and len(movers) == 1, st
    assert movers["n_exposures"].iloc[0] == 2 and movers["flux_ujy"].iloc[0] == 600.0
    monkeypatch.setattr(C, "FIXED_VETO_BRIGHT_RATIO", None)
    assert len(sc.run()[1]) == 0  # released, then dropped by the plain veto
    monkeypatch.setattr(C, "FIXED_VETO_BRIGHT_RATIO", 3.0)
    monkeypatch.setattr(C, "FIXED_RELEASE_FLUX_RATIO", None)
    assert len(sc.run()[1]) == 0  # never released: a member of the star


def test_saturated_dot_is_not_released_as_bright():
    from skyshift_core import SAT_BITS
    sc = Scene().star(*STAR)
    sc.track(*offset_radec(*STAR, 2.5, 0.0), 2, 1.5, [0, 1], flux=600.0, snr=30.0, flags_peak=int(SAT_BITS & -SAT_BITS))
    assert len(sc.run()[1]) == 0


def test_offset_session_is_released_only_its_off_dots(monkeypatch):
    """B1 (Ljuba s242): a session whose dots sit 2–6″ off the group's other sessions leaves the group; in a session with
    the star's own dots too, those stay members."""
    from skyshift_pipeline.link import fixed_groups
    sc = Scene().star(*STAR, sessions=[0, 1, 3, 4])                       # rows 0–7
    sc.track(*offset_radec(*STAR, 3.9, 0.0), 2, 1.0, [0, 1], snr=50.0)  # rows 8, 9: 3.9″, 4.9″ (chained, not far)
    sc.add(*STAR, 2, 0, snr=200)                                        # row 10: the star in that session too
    d = pd.DataFrame(sc.rows)
    st: dict = {}
    _, sid = fixed_groups(d, None, st)
    ast = np.isin(np.arange(len(d)), [8, 9])
    assert (sid[ast] == -1).all() and (sid[~ast] == sid[0]).all() and sid[0] >= 0
    assert st["release_offset_dots"] == 2 and st["release_far_dots"] == 0
    monkeypatch.setattr(C, "FIXED_RELEASE_SESSION_ARCSEC", None)
    _, sid = fixed_groups(d)
    assert (sid == sid[0]).all()


def test_slow_mover_stays_one_fixed_source(monkeypatch):
    """A slow mover (a distant planet, 1.2″ per session here) drifts through every session of its visit: no session is
    an outlier (FIXED_RELEASE_SESSION_SPREAD), so it stays one single-visit fixed source for `orphans`."""
    from skyshift_pipeline.link import fixed_groups
    sc = Scene(n_sessions=8)
    for s in range(8):
        for e in range(2):
            sc.add(*offset_radec(*STAR, 1.2 * s, 0.0), s, e, snr=50.0)
    d = pd.DataFrame(sc.rows)
    fixed, sid = fixed_groups(d)
    assert len(fixed) == 1 and (sid == sid[0]).all()
    monkeypatch.setattr(C, "FIXED_RELEASE_SESSION_SPREAD", None)
    fixed, sid = fixed_groups(d)
    assert (sid == -1).sum() == 4  # the two end sessions (4.2″ off the median) would have been cut off


def test_released_fragment_group_is_undone_unless_catalogued():
    """B3: released dots that form a group of >= 3 sessions stay a fixed source only when catalogued or seen in >= half
    of the parent's sessions; wing fragments of a star go back to it."""
    from skyshift_pipeline.link import release_far_members
    sc = Scene(n_sessions=8).star(*STAR, sessions=range(8))
    frag = offset_radec(*STAR, 7.5, 0.0)
    for s in range(3):
        sc.add(*frag, s, 0)
    d = pd.DataFrame(sc.rows)
    lab = np.zeros(len(d), np.int64)  # one FoF group (as if chained)
    st: dict = {}
    out = release_far_members(d, lab, None, st)
    assert (out == 0).all() and st["release_far_dots"] == 3 and st["release_groups_undone"] == 1
    cat = pd.DataFrame(dict(ra=[frag[0]], dec=[frag[1]], epoch_mjd=[t(0)], pmra=[0.0], pmdec=[0.0]))
    out = release_far_members(d, lab, cat, st)
    assert (out[-3:] != 0).all() and len(set(out[-3:])) == 1 and (out[:-3] == 0).all() and st["release_groups_kept"] == 1
    bright = d.copy()
    bright.loc[bright.index[-3:], "flux_ujy"] = 1000.0  # released by flux too: free, one label per session
    out = release_far_members(bright, lab, None, st)
    assert (out[-3:] != 0).all() and len(set(out[-3:])) == 3 and (out[:-3] == 0).all()


def test_catalogued_moves_entries_by_proper_motion():
    from skyshift_pipeline.link import catalogued
    pos = (122.0, 16.0)
    old = offset_radec(*pos, -10.0, 0.0)  # 10″ west at an epoch 10 years earlier, moving 1″/yr east
    cat = pd.DataFrame(dict(ra=[old[0]], dec=[old[1]], epoch_mjd=[t(0) - 3652.5], pmra=[1000.0], pmdec=[0.0]))
    assert catalogued(np.array([pos[0]]), np.array([pos[1]]), np.array([t(0)]), cat).tolist() == [True]
    assert catalogued(np.array([pos[0]]), np.array([pos[1]]), np.array([t(0)]), cat[["ra", "dec"]]).tolist() == [False]
    assert catalogued(np.array([pos[0]]), np.array([pos[1]]), np.array([t(0)]), None).tolist() == [False]


def test_track_merged_into_another_dot_is_not_missed(monkeypatch):
    """B2: in an exposure where the track passes within 15″ of another detection (a star here, 12″ off), the asteroid's
    own dot merges into it: that exposure doesn't count as missed."""
    sc = Scene(n_exp=3)
    a = (122.0, 16.0)
    sc.track(*a, 2, 1.5, [0, 1])
    star = offset_radec(*a, 3.0, 12.0)  # 12″ from the track's place in exposure 2, in both detectors of each photo
    for s in range(5):
        for e in range(3):
            sc.add(*star, s, e, snr=200).add(*star, s, e, det=4, snr=200)
    _, movers, st = sc.run()
    assert len(movers) == 1 and movers["missed_exposures"].iloc[0] == 0, st
    monkeypatch.setattr(C, "MOVER_COVER_BLEND_ARCSEC", None)
    monkeypatch.setattr(C, "MOVER_COVER_BRIGHT", None)
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["vetoed_missed"] == 1


def test_one_miss_allowed_when_both_exposures_have_a_detector_pair():
    """B2: two exposures each confirmed by a simultaneous detector pair (D1+D4) may miss one covering exposure."""
    sc = Scene(n_exp=3)
    a = (122.0, 16.0)
    for e in (0, 1):
        p = offset_radec(*a, 1.5 * e, 0.0)
        sc.add(*p, 2, e, det=1).add(*p, 2, e, det=4)
    sc.track(122.1, 16.0, 2, 1.5, [0, 1])  # the same with one detector: vetoed
    _, movers, st = sc.run()
    assert len(movers) == 1 and movers["paired_exposures"].iloc[0] == 2 and movers["missed_exposures"].iloc[0] == 1
    assert st["vetoed_missed"] == 1


def test_track_near_a_photo_edge_does_not_cover():
    """B2: a photo holding the track's place less than MOVER_COVER_EDGE_PX (25 px) inside doesn't count as a miss."""
    sc = Scene(n_exp=3)
    a = (122.0, 16.15)
    sc.track(*a, 2, 1.5, [0, 1])
    p2 = offset_radec(*a, 3.0, 0.0)
    c = (1.75 - 15 * C.PIXEL_ARCSEC / 3600) / np.cos(np.deg2rad(16.15))  # exposure 2's photo: 15 px inside its east edge
    photos = [dict(p, s_region=square(p2[0] - c, 16.15)) if p["obs_id"] == "S2E2" else p for p in sc.photos]
    _, movers, st = sc.run(photos)
    assert len(movers) == 1 and movers["cover_exposures"].iloc[0] == 2, st


def test_track_leaving_the_zone_box_does_not_cover():
    """B2/B5: link clips dots at the zone box, so an exposure whose track position is outside the box (less 10″) can't
    show the asteroid."""
    from skyshift_pipeline.link import classify as cls
    zone = C.Zone("t", 120.0, 124.0, 14.0, 16.0 + 13.5 / 3600)
    sc = Scene(n_exp=3)
    a = (122.0, 16.0)
    for e in (0, 1):  # north 2″ per exposure: 13.5″, 11.5″ inside the box's north edge; exposure 2 at 9.5″
        sc.add(*offset_radec(*a, 0.0, 2.0 * e), 2, e)
    d, p = pd.DataFrame(sc.rows), pd.DataFrame(sc.photos)
    _, movers, st = cls(d, p, zone=zone)
    assert len(movers) == 1 and movers["cover_exposures"].iloc[0] == 2, st
    _, movers, st = cls(d, p)
    assert len(movers) == 0 and st["vetoed_missed"] == 1


def test_fast_mover_in_one_detector_links_on_a_three_exposure_line(monkeypatch):
    """A 300″/h mover seen in one detector only (10.5″ per 2.1 min, beyond 3″ + 1″/min): three exposures on a straight,
    evenly spaced line link (PAIR_FAST_RATE_ARCSEC_PER_MIN); two exposures, or three off the line, don't. Above
    PAIR_FAST_TRAIL_MIN_ARCSEC_H (400″/h) the dots must look trailed: 600″/h with elong 2.6 links, with 1.1 doesn't."""
    step = 300.0 * 2.1 / 60.0
    sc = Scene(n_exp=3)
    sc.track(122.0, 16.0, 2, step, [0, 1, 2])
    sc.track(122.1, 16.1, 3, step, [0, 1])                               # two exposures only
    for e, x in zip(range(3), (0.0, step, 3 * step)):                    # third dot 10.5″ off the line
        sc.add(*offset_radec(122.2, 16.2, x, 0.0), 4, e)
    sc.track(122.3, 16.0, 1, 2 * step, [0, 1, 2])                        # 600″/h, point-like dots
    sc.track(122.3, 16.2, 0, 2 * step, [0, 1, 2], elong=2.6)             # 600″/h, trailed dots
    _, movers, st = sc.run()
    assert len(movers) == 2 and st["movers_fast_rule"] == 2, st
    m = movers.sort_values("rate_arcsec_h")
    assert (m["n_exposures"] == 3).all() and np.allclose(m["rate_arcsec_h"], [300.0, 600.0], rtol=1e-3)
    assert m["dec"].iloc[1] == pytest.approx(16.2, abs=1e-3)
    monkeypatch.setattr(C, "PAIR_FAST_TRAIL_MIN_ARCSEC_H", None)
    assert len(sc.run()[1]) == 3
    monkeypatch.setattr(C, "PAIR_FAST_RATE_ARCSEC_PER_MIN", None)
    _, movers, st = sc.run()
    assert len(movers) == 0 and st["movers_fast_rule"] == 0


def test_shuffled_exposure_control_breaks_real_links():
    """control_movers moves each exposure's dots (and footprints) 300″ in its own direction: real movers, slow or fast,
    are not linked any more."""
    from skyshift_pipeline.link import control_counts, control_movers, control_offsets, fixed_groups, free_dots
    sc = Scene(n_exp=3).star(*STAR)
    sc.track(122.0, 16.0, 2, 1.5, [0, 1, 2])
    sc.track(122.0, 16.3, 1, 300.0 * 2.1 / 60.0, [0, 1, 2])
    d, p = pd.DataFrame(sc.rows), pd.DataFrame(sc.photos)
    _, movers, _ = classify(d, p)
    assert len(movers) == 2
    s, _, de, dn = control_offsets(p)
    assert np.allclose(np.hypot(de, dn), C.LINK_CONTROL_OFFSET_ARCSEC) and len(s) == 15
    for k in np.unique(s):  # the exposures of a session point in different directions
        a = np.arctan2(de[s == k], dn[s == k])
        assert np.unique(np.round(a, 6)).size == (s == k).sum()
    fixed, sid = fixed_groups(d)
    cm, _ = control_movers(free_dots(d, sid, fixed), d, p, None)
    assert control_counts(cm)["two_exposures"] == 0 and control_counts(cm)["fast"] == 0


def test_variable_star_bright_session_stays_in_its_group(monkeypatch):
    """FIXED_RELEASE_FLUX_SAFE: a star 5× brighter for one session ON its position (a variable) keeps those dots in its
    group (the Alekto case, 2.5–4″ off, is still released: test_bright_asteroid_chained_into_a_fainter_star)."""
    from skyshift_pipeline.link import fixed_groups
    sc = Scene().star(*STAR)
    for e in range(2):
        sc.rows[2 * 2 + e].update(flux_ujy=500.0)  # session 2 of the star: 5× (rows are session-major, 2 exposures each)
    d = pd.DataFrame(sc.rows)
    st: dict = {}
    fixed, sid = fixed_groups(d, None, st)
    assert len(fixed) == 1 and (sid >= 0).all() and st["release_bright_dots"] == 0 and st["release_bright_kept_variable"] == 2
    assert len(sc.run()[1]) == 0
    monkeypatch.setattr(C, "FIXED_RELEASE_FLUX_SAFE", False)
    fixed, sid = fixed_groups(d, None, st)
    assert st["release_bright_dots"] == 2 and (sid == -1).sum() == 2


def test_off_star_member_dots_go_to_the_nonfixed_table():
    """off_star_members: a member dot well off its star (an asteroid crossing it, chained in by the 4″ FoF) or a second dot
    of the group in one photo > 1.5″ off; not saturated, not elongated, not repeated at one place in another session."""
    from skyshift_core import SAT_BITS
    from skyshift_pipeline.link import nonfixed_dots_table, off_star_members
    rows = []
    for s in range(5):  # the star, one photo per session
        rows.append(dict(ra=STAR[0], dec=STAR[1], session=s + 1, photo=s, snr=200.0, flags_peak=0, elong=1.1))
    near = offset_radec(*STAR, 2.5, 0.0)
    rows.append(dict(ra=near[0], dec=near[1], session=3, photo=2, snr=20.0, flags_peak=0, elong=1.1))   # 5: second in photo
    far = offset_radec(*STAR, 0.0, 3.8)
    rows.append(dict(ra=far[0], dec=far[1], session=5, photo=6, snr=60.0, flags_peak=0, elong=1.1))    # 6: alone, 3.8″ off
    wing = offset_radec(*STAR, -2.5, 0.0)
    for s in (1, 4):                                                                                   # 7, 8: repeats
        rows.append(dict(ra=wing[0], dec=wing[1], session=s + 1, photo=s, snr=20.0, flags_peak=0, elong=1.1))
    sat = offset_radec(*STAR, 0.0, -3.0)                                                               # 9: saturated
    rows.append(dict(ra=sat[0], dec=sat[1], session=2, photo=1, snr=20.0, flags_peak=int(SAT_BITS & -SAT_BITS), elong=1.1))
    rows.append(dict(ra=near[0], dec=near[1] + 1 / 3600, session=4, photo=3, snr=20.0, flags_peak=0, elong=4.0))  # 10
    d = pd.DataFrame(rows).assign(mjd=61000.0, visit=1, detector=1, flux_ujy=100.0, flags=0)
    d["dot_row"] = np.arange(len(d), dtype=np.int32)
    sid = np.full(len(d), 7)
    fixed = pd.DataFrame({"source_id": [7], "ra": [STAR[0]], "dec": [STAR[1]]})
    assert np.flatnonzero(off_star_members(d, sid, fixed)).tolist() == [5, 6]
    photos = pd.DataFrame({"obs_id": [f"P{i}" for i in range(7)], "detector": 1})
    fd = nonfixed_dots_table(d, sid, fixed, photos)
    assert sorted(fd["dot_row"].tolist()) == [5, 6] and fd["in_fixed"].all() and fd["near_fixed"].all()
    sid2 = sid.copy()
    sid2[6] = -1  # an ordinary free dot: in_fixed False
    fd2 = nonfixed_dots_table(d, sid2, fixed, photos)
    assert fd2.set_index("dot_row")["in_fixed"].to_dict() == {5: True, 6: False}


# --- session-stack rows (SESSION_STACK on) -------------------------------------------------------------------------
def write_stack(store, key, obs_ids, rows, moving=None):
    """One session's stack files in coadd's format: rows = [dict(stack_row, ra, dec, ...)] over SOURCE_COLUMNS
    defaults; moving = per-photo rows (MOVING_COLUMNS) of its moving sources."""
    from skyshift_pipeline import coadd as CO
    base = {c: (False if d == "bool" else 0) for c, d in CO.SOURCE_COLUMNS.items()}
    src = pd.DataFrame([{**base, "detector": 1, "n_exp": len(obs_ids), "n_exp_session": len(obs_ids), "snr": 5.0,
                         "snr_mf": 5.5, "flux_ujy": 20.0, "pos_err_arcsec": 0.4, **r} for r in rows])
    src = src.astype(CO.SOURCE_COLUMNS)[list(CO.SOURCE_COLUMNS)]
    if moving is not None:
        mv = pd.DataFrame([{**{c: 0 for c in CO.MOVING_COLUMNS}, "pos_ok": True, "flux_err_ujy": 4.0, **m} for m in moving])
        store.write_bytes(CO.session_moving_rel(key), CO._parquet(mv[CO.MOVING_COLUMNS]))
    store.write_bytes(CO.session_rel(key), CO._parquet(src, {"detector": 1, "obs_ids": list(obs_ids)}))


def test_link_takes_session_stack_rows_only_when_switched_on(tmp_path, monkeypatch):
    import json

    from test_cli import dots_df

    from skyshift_pipeline import coadd as CO
    from skyshift_pipeline.cli import main
    from skyshift_pipeline.photolist import add_sessions
    from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel
    store = Store(str(tmp_path))
    faint, glitch, mov = (122.25, 16.12), (122.35, 16.18), (122.20, 16.22)
    rows = [dict(obs_id=f"S{s}E{e}", detector=1, mjd=t(s, e), s_region=square(122.3, 16.15), collection="spherex_dr1",
                 url="", s3_key="", size_bytes=1) for s in range(5) for e in range(2)]
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel("pilot"), photos)
    for p in photos.itertuples():
        store.write_parquet(dots_rel(p.obs_id, 1), dots_df(p.obs_id, 1, p.mjd, [STAR]))
    idx = []
    for s in range(5):
        ids = [f"S{s}E0", f"S{s}E1"]
        key = CO.session_key(ids, 1)
        mjd = (t(s, 0) + t(s, 1)) / 2
        srows = [dict(stack_row=0, ra=STAR[0], dec=STAR[1], ra_psf=STAR[0], dec_psf=STAR[1], snr=60.0, mjd=mjd),
                 dict(stack_row=1, ra=faint[0] + 1e-5, dec=faint[1], ra_psf=faint[0], dec_psf=faint[1], mjd=mjd)]
        moving = None
        if s == 1:  # a one-photo glitch: never a link row
            srows.append(dict(stack_row=2, ra=glitch[0], dec=glitch[1], ra_psf=np.nan, dec_psf=np.nan, glitch=True,
                              glitch_share=0.95, mjd=mjd))
        if s == 3:  # a mover too faint for one photo's dots: its photo positions become pseudo-dots
            srows.append(dict(stack_row=3, ra=mov[0], dec=mov[1], ra_psf=mov[0], dec_psf=mov[1], motion_snr=8.0,
                              rate_arcsec_h=60.0, mjd=mjd))
            moving = [dict(stack_row=3, photo=e, obs_id=ids[e], mjd=t(s, e),
                           ra=offset_radec(mov[0], mov[1], 2.1 * (e - 0.5), 0.0)[0], dec=mov[1], flux_ujy=20.0)
                      for e in range(2)]
        write_stack(store, key, ids, srows, moving)
        idx += [dict(key=key, session=s + 1, visit=1, detector=1, obs_id=o, n_exp_session=2) for o in ids]
    store.write_parquet(CO.index_rel("pilot"), pd.DataFrame(idx))

    def run_link():
        assert main(["link", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
        out = {n: store.read_parquet(results_rel("pilot", f"{n}.parquet"))
               for n in ("fixed", "members", "movers", "nonfixed_dots")}
        has = store.exists(results_rel("pilot", "stack_members.parquet"))
        return out, has, json.loads((tmp_path / "results/pilot/summary.json").read_text())["steps"]["link"]

    monkeypatch.setattr(C, "SESSION_STACK", False)
    off, has_off, st_off = run_link()
    assert len(off["fixed"]) == 1 and len(off["movers"]) == 0 and not has_off and "session_stack" not in st_off
    monkeypatch.setattr(C, "SESSION_STACK", True)
    on, has_on, st_on = run_link()
    sm = store.read_parquet(results_rel("pilot", "stack_members.parquet"))
    assert has_on and len(on["fixed"]) == 2
    # the faint source: one fixed group of 5 stack rows and no real dot; the star's stack rows were its dots already
    assert len(sm) == 5 and sm["source_id"].nunique() == 1 and (sm["stack_row"] == 1).all()
    pd.testing.assert_frame_equal(on["members"], off["members"])  # members.parquet: real dots only, unchanged
    ss = st_on["session_stack"]
    assert ss["stack_rows_glitch"] == 1 and ss["stack_rows_dedup"] == 5 and ss["fixed_stack_only"] == 1
    assert ss["stack_pseudo_dots_used"] == 2 and ss["movers_with_stack_dots"] == 1
    assert len(on["movers"]) == 1 and int(on["movers"]["session"].iloc[0]) == 4
    assert "with_stack_dots" in st_on["control"]
    # nonfixed_dots (tracklets' recovery): the free stack row (the mover's; the faint source is a fixed group) joins
    # the real dots as obs_id "", dot_row −1 with its stack key and row; switched off, the table has no stack columns
    assert "stack_row" not in off["nonfixed_dots"]
    nf = on["nonfixed_dots"]
    stk = nf[nf["stack_row"] >= 0]
    assert len(stk) == 1 and int(stk["stack_row"].iloc[0]) == 3 and int(stk["session"].iloc[0]) == 4
    assert stk["obs_id"].iloc[0] == "" and int(stk["dot_row"].iloc[0]) == -1 and stk["stack_key"].iloc[0] != ""
    assert (nf.loc[nf["stack_row"] < 0, "stack_key"] == "").all() and ss["nonfixed_stack_rows"] == 1
