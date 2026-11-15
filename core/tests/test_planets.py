"""skyshift_core.planets: astropy's built-in ephemeris against JPL Horizons, and real movers of the 15 Nov
candidates.json that are planets or moons (each identified with Horizons within 2″)."""

from __future__ import annotations

import numpy as np
import pytest

from skyshift_core.planets import (
    PLANETS,
    RADIUS_ARCSEC,
    SELF_ARCSEC,
    _sep_arcsec,
    near_planet,
    near_planets,
    planet_label,
    positions,
)

MJDS = (60780.0, 60850.0, 60920.0, 60990.0, 61060.0, 61130.0, 61200.0, 61270.0, 61340.0)  # 2025-04-15 .. 2026-10-27
# JPL Horizons API (ssd.jpl.nasa.gov/api/horizons.api), CENTER 500@399 (geocentre), QUANTITIES 1 (astrometric RA/Dec,
# ICRF), TIME_TYPE UT, at 00:00 UTC on those dates; fetched 15 Nov 2026.
HORIZONS = {
    "Mars": [(120.975064362, 22.769335886), (155.728251149, 11.295832901), (195.025515397, -6.152871067),
             (242.213256263, -21.477566962), (299.255102321, -21.730411374), (353.041408707, -4.146557629),
             (42.994266849, 15.897679876), (94.374056932, 23.704007671), (138.614304428, 17.561216331)],
    "Jupiter": [(76.954730248, 22.620022250), (93.121517429, 23.264476738), (109.169536982, 22.235545960),
                (116.737717148, 21.274071443), (110.025100472, 22.444846816), (106.700344157, 22.921109098),
                (117.282808501, 21.495307987), (132.923701831, 18.133844962), (145.872469534, 14.382417150)],
    "Saturn": [(356.904651905, -3.478703323), (2.042442839, -1.517954604), (0.610275611, -2.440349450),
               (356.427100849, -4.201445915), (358.283267331, -3.144134031), (5.488456082, 0.056611544),
               (12.402960240, 2.832982408), (13.855900868, 3.107404837), (9.542155306, 1.179742617)],
    "Uranus": [(52.804400456, 18.824099869), (56.838702933, 19.730311715), (59.007514260, 20.170252737),
               (57.362072075, 19.840586046), (54.952385790, 19.341457432), (56.149056923, 19.623350557),
               (60.105474404, 20.434745159), (63.177400891, 20.991410424), (62.530167026, 20.882587210)],
    "Neptune": [(0.704339427, -1.078257707), (2.163125620, -0.498958343), (1.435564910, -0.869306220),
                (359.851716002, -1.550933483), (0.045104567, -1.420630993), (2.175082080, -0.482726552),
                (4.048811962, 0.284968771), (3.861435291, 0.141192471), (2.225115937, -0.579460427)],
}
# measured on those dates: Mars max 7.1″, Jupiter 20.4″, Saturn 19.9″, Uranus 6.5″, Neptune 4.1″
TOLERANCE_ARCSEC = {"Mars": 10.0, "Jupiter": 25.0, "Saturn": 25.0, "Uranus": 10.0, "Neptune": 6.0}


@pytest.mark.parametrize("name", PLANETS)
def test_positions_match_horizons(name):
    pos = positions(np.array(MJDS))
    ra, dec = np.array(HORIZONS[name]).T
    sep = _sep_arcsec(pos[name][0], pos[name][1], ra, dec)
    assert sep.max() < TOLERANCE_ARCSEC[name], sep.round(1)


def test_tolerances_are_small_against_the_radii():
    for name in PLANETS:
        assert TOLERANCE_ARCSEC[name] < SELF_ARCSEC[name] < RADIUS_ARCSEC[name]


# (id, ra, dec, mjd, planet, label): movers listed in candidates.json until 15 Nov, with Horizons' identification
REAL = [
    ("ecl35-515240213288-87871772", 356.404831, -4.126031, 61022.064174, "Saturn", "a moon of Saturn"),  # Iapetus, grade A
    ("ecl35-516850213636-87875974", 356.868766, -3.85798, 61024.981621, "Saturn", "a moon of Saturn"),  # Phoebe, 31′ out
    ("ecl05-660930033194-87711048", 58.933291, 20.15587, 60910.449783, "Uranus", "Uranus"),  # Uranus itself
    ("ecl06-666030035444-88237443", 63.279236, 21.005489, 61276.002328, "Uranus", "a moon of Uranus"),  # Titania
    ("ecl00-542080002541-88156802", 4.236593, 0.347145, 61220.00173, "Neptune", "Neptune"),  # Neptune itself
    ("ecl00-542060002544-88158267", 4.240318, 0.344109, 61221.018648, "Neptune", "a moon of Neptune"),  # Triton, 14″
    ("ecl11-668350064626-87790077", 115.681692, 21.392066, 60965.331308, "Jupiter", "a moon of Jupiter"),  # Europa
    ("ecl11-665670064749-87793893", 115.551213, 20.945184, 60967.981055, "Jupiter", "a moon of Jupiter"),  # Himalia, 31′
]


@pytest.mark.parametrize("cid,ra,dec,mjd,planet,label", REAL, ids=[r[0] for r in REAL])
def test_real_planet_and_moon_movers(cid, ra, dec, mjd, planet, label):
    hit = near_planet(ra, dec, mjd)
    assert hit is not None and hit[0] == planet
    assert planet_label(*hit) == label


def test_a_hunt_mover_far_from_every_planet():
    assert near_planet(122.250475, 20.897236, 61142.911622) is None  # hunt-665380068525-88045793
    # Jupiter's own spot, a year later: Jupiter has moved on (~30°)
    assert near_planet(115.551213, 20.945184, 60967.981055 + 365.0) is None


def test_radius_edges():
    mjd = 61022.0
    ra, dec = (float(x[0]) for x in positions(mjd)["Saturn"])
    r = RADIUS_ARCSEC["Saturn"]
    for off, inside in ((r - 30.0, True), (r + 30.0, False)):
        hit = near_planet(ra, dec + off / 3600.0, mjd)
        assert (hit is not None) == inside
        if inside:
            assert hit[0] == "Saturn" and hit[1] == pytest.approx(off, abs=0.5)
    assert near_planet(ra, dec, mjd) == ("Saturn", pytest.approx(0.0, abs=0.01))
    assert planet_label("Saturn", 0.0) == "Saturn"


def test_arrays_nan_and_empty():
    names, sep = near_planets([4.236593, 122.250475, np.nan], [0.347145, 20.897236, 0.0],
                              [61220.00173, 61142.911622, 61220.0])
    assert list(names) == ["Neptune", "", ""]
    assert sep[0] < SELF_ARCSEC["Neptune"] and np.isnan(sep[1:]).all()
    names, sep = near_planets([], [], [])
    assert names.shape == (0,) and sep.shape == (0,)
