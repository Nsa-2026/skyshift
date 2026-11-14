import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline.link import classify, pair_candidates
from skyshift_pipeline.sky import Footprint, offset_radec

from conftest import t
from test_photolist import square

STAR = (122.30, 16.10)


class Scene:
    """Synthetic in-zone dots in link's load format, with N sessions of 2 exposures, all covering
    the field (photos list with footprints and sessions)."""

    def __init__(self, n_sessions=5):
        self.rows = []
        self.photos = []
        for s in range(n_sessions):
            for e in range(2):
                for det in (1, 4):
                    self.photos.append(dict(obs_id=f"S{s}E{e}", detector=det, mjd=t(s, e), session=s + 1, visit=1,
                                            s_region=square(122.3, 16.15)))

    def add(self, ra, dec, s, e, det=1, snr=20.0, elong=1.1, flags=0, flags_peak=0):
        self.rows.append(dict(detector=det, mjd=t(s, e), ra=ra, dec=dec, flux_ujy=100.0, snr=snr, flags=flags,
                              flags_peak=flags_peak, elong=elong, session=s + 1, visit=1, photo=0, exposure=s * 10 + e))
        return self

    def star(self, ra, dec, sessions=range(5)):
        for s in sessions:
            for e in range(2):
                self.add(ra, dec, s, e, snr=200)
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
    _, movers, _ = sc.run()
    assert len(movers) == 1
    m = movers.iloc[0]
    assert m["evidence"] == "two_detectors" and m["detectors"] == "1,4" and m["det"] == 1


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
