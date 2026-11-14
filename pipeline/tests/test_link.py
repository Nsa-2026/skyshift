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
    sc = Scene()
    pos = (122.0, 16.0)
    sc.add(*pos, 2, 0).add(*offset_radec(*pos, 1.0, 0.0), 2, 1)
    sc.add(*pos, 4, 0)  # a single stray dot at the same place in another session
    _, movers, st = sc.run()
    assert len(movers) == 1 and st["fixed"] == 0


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
