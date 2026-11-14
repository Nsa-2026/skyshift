import numpy as np
from astropy.time import Time

from skyshift_core import sessions, summarize_visits, visits

MIN = 1 / 1440


def mjd(iso: str) -> float:
    return float(Time(iso, scale="utc").mjd)


def season(start_iso: str, end_iso: str, rng: np.random.Generator) -> list[float]:
    """Sessions of 1-5 exposures 2.1 min apart, sessions hours to days apart."""
    t, end, out = mjd(start_iso), mjd(end_iso), []
    while t <= end:
        for k in range(int(rng.integers(1, 6))):
            out.append(t + k * 2.1 * MIN)
        t += rng.uniform(0.2, 2.0)
    out[-1] = end  # last exposure exactly at the end date
    return out


def test_ra125_boundary_case_may_2026():
    """RA 125.5: visits Oct-Nov 2025 and Apr-6 May 2026. A 1 May cut must not split visit 2."""
    rng = np.random.default_rng(0)
    t = np.array(season("2025-10-20", "2025-11-12", rng) + season("2026-04-14", "2026-05-06T12:00", rng))
    rng.shuffle(t)  # input order must not matter
    v = visits(t)
    assert set(v) == {1, 2}
    assert np.all(v[t < mjd("2026-01-01")] == 1)
    late = t >= mjd("2026-04-01")
    assert np.all(v[late] == 2)
    assert (t[late] > mjd("2026-05-01")).any()  # exposures after 1 May are in the same visit
    s = summarize_visits(t)
    assert [x.visit for x in s] == [1, 2]
    assert s[1].mjd_end == mjd("2026-05-06T12:00")
    assert s[0].n_exposures + s[1].n_exposures == t.size


def test_gap_rule_and_75_day_rule_together():
    # gap 70 d > 60: new visit
    assert list(visits([60000.0, 60010.0, 60080.0])) == [1, 1, 2]
    # a 50-day season (gap 49.9 d < 60) stays one visit: seasons of 45-55 days are normal
    assert list(visits([60000.0, 60000.1, 60050.0])) == [1, 1, 1]
    # 80 days > 75: cut into ceil(80/30) = 3 equal parts of 26.7 days
    assert list(visits([60000.0, 60000.1, 60040.0, 60080.0])) == [1, 1, 2, 3]


def test_barnard_like_season_has_no_leftover_visit():
    """Real case (Barnard's Star, Aug-Sep 2025): 55 exposures in 30 days, then 4 more on days 46-55.
    The first rule made those 4 a visit of their own with one detector pair; now it is one visit."""
    rng = np.random.default_rng(1)
    t = np.array(season("2025-08-15", "2025-09-14", rng) + [mjd("2025-09-30"), mjd("2025-10-09")])
    assert set(visits(t)) == {1}


def test_near_pole_long_visit_split_in_equal_parts():
    """Near the ecliptic pole a spot is seen continuously: 100 days -> 4 equal parts of 25 days."""
    base = 60800.0
    starts = base + np.arange(0, 100.5, 0.5)  # a session every 12 h for 100 days
    t = np.concatenate([starts + k * 2.1 * MIN for k in range(3)])
    v = visits(t)
    assert sorted(set(v)) == [1, 2, 3, 4]
    for vid, (a, b) in zip([1, 2, 3, 4], [(0, 25), (25, 50), (50, 75), (75, 101)]):
        # parts are (last - first) / 4 = 25.0007 d long, so skip sessions right on a boundary
        sel = (t - base >= a + 0.25) & (t - base < b - 0.25)
        assert np.all(v[sel] == vid)
    # a session is never split across visits
    s = sessions(t)
    for sid in np.unique(s):
        assert len(set(v[s == sid])) == 1
    # a following normal visit continues the numbering
    t2 = np.concatenate([t, [base + 300, base + 300.5]])
    assert visits(t2).max() == 5


def test_75_day_visit_not_split():
    t = np.linspace(60000, 60075, 50)
    assert set(visits(t)) == {1}


def test_sessions_rule():
    t = np.array([0, 2.1, 4.2, 9.9 + 4.2, 30, 32.1]) * MIN + 60000  # gaps 2.1, 2.1, 9.9, 15.9, 2.1 min
    assert list(sessions(t)) == [1, 1, 1, 1, 2, 2]
    assert list(sessions(np.array([0, 10.01]) * MIN + 60000)) == [1, 2]  # 10 min or more: new session
    assert list(sessions(t[::-1])) == [2, 2, 1, 1, 1, 1]


def test_empty_and_single():
    assert visits([]).size == 0 and sessions([]).size == 0
    assert list(visits([60000.0])) == [1] and list(sessions([60000.0])) == [1]
