import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline.link import classify, cluster_ids
from skyshift_pipeline.sky import offset_radec

from conftest import DotMaker, t, tiles_for

STAR = (121.0, 15.0)
MOVER = (121.2, 15.2)
GLITCH = (121.4, 15.4)
PAIR = (121.6, 15.6)
THIN = (121.8, 15.8)      # tile covered by only 2 sessions
TRAIL = (122.0, 16.0)


def scene() -> tuple[pd.DataFrame, pd.DataFrame]:
    m = DotMaker()
    for s in range(4):                       # a star in 4 sessions, 2 exposures each
        for e in range(2):
            m.add(*STAR, t(s, e), snr=200)
    # an asteroid in session 2: two exposures 2.1 min apart, moved 1.4″
    r2, d2 = offset_radec(*MOVER, 1.0, 1.0)
    m.add(*MOVER, t(2, 0), snr=12).add(r2, d2, t(2, 1), snr=11)
    m.add(*GLITCH, t(1, 0), snr=30)          # a single-photo glitch
    m.add(*PAIR, t(3, 0), det=1, snr=9).add(*PAIR, t(3, 0), det=4, snr=8)  # same moment, D1 + D4
    m.add(*THIN, t(1, 0)).add(*THIN, t(1, 1))
    m.add(*TRAIL, t(0, 0), elong=5).add(*TRAIL, t(0, 1), elong=5)
    tiles = tiles_for([(*STAR, 30), (*MOVER, 30), (*GLITCH, 30), (*PAIR, 30), (*THIN, 2), (*TRAIL, 30)])
    return m.frame(), tiles


def test_clusters_join_neighbouring_cells_only():
    ra = np.array([121.0, 121.0 + 4 / 3600, 121.0 + 60 / 3600])
    dec = np.array([15.0, 15.0, 15.0])
    lab = cluster_ids(ra, dec)
    assert lab[0] == lab[1] != lab[2]


def test_classify_scene():
    dots, tiles = scene()
    fixed, movers, st = classify(dots, tiles)
    assert len(fixed) == 1
    assert abs(fixed["ra"].iloc[0] - STAR[0]) < 1e-6 and fixed["n_sessions"].iloc[0] == 4
    assert len(movers) == 2, movers
    by_ev = movers.set_index("evidence")
    mv = by_ev.loc["two_exposures"]
    assert abs(mv["ra"] - MOVER[0]) < 1e-3 and mv["n_dots"] == 2 and mv["n_exposures"] == 2
    assert mv["snr"] == 12
    pr = by_ev.loc["two_detectors"]
    assert abs(pr["ra"] - PAIR[0]) < 1e-6 and pr["detectors"] == "1,4"
    assert st["dropped_under_covered"] == 1      # THIN
    assert st["dropped_one_photo"] == 1          # TRAIL: elongated dots aren't mover evidence
    assert st["single_dot_groups"] == 1          # GLITCH
    assert movers["cell"].dtype == np.int64 and movers["flags"].dtype == np.int32


def test_two_detectors_needs_a_simultaneous_pair():
    m = DotMaker()
    m.add(*PAIR, t(3, 0), det=1).add(*PAIR, t(3, 0), det=2)   # D1 + D2 is not a simultaneous pair
    _, movers, st = classify(m.frame(), tiles_for([(*PAIR, 30)]))
    assert len(movers) == 0 and st["dropped_one_photo"] == 1


def test_two_sessions_is_neither():
    m = DotMaker()
    m.add(*STAR, t(0)).add(*STAR, t(1))
    fixed, movers, st = classify(m.frame(), tiles_for([(*STAR, 30)]))
    assert len(fixed) == 0 and len(movers) == 0 and st["dropped_two_sessions"] == 1
    assert C.FIXED_MIN_SESSIONS == 3
