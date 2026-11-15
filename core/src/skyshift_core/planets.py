"""Planets and their moons: known bodies the MPC orbit files don't hold.

The known-object check (known_index, the pipeline's `known` step) uses MPC asteroid and comet orbits only, so Mars,
the giant planets and their moons pass as unknown fast movers. 15 Nov candidates.json: 131 of its 1,885 movers lie
within the radii below, 54 of its 56 grade A among them. 113 were identified one by one with JPL Horizons (all within
2.1″ but one at 4.1″): Uranus ×12, Titania ×6, Oberon ×17, Neptune ×12, Triton, Titan ×6, Iapetus ×17, Rhea,
Hyperion, Phoebe ×11, Europa ×4, Ganymede ×5, Callisto ×6, Himalia ×13, Carme; the other 18 (grade B or C) lie 14–67′
from Jupiter. near_planet() says whether a position lies within a planet's radius at a time: the planet itself, one of
its moons, or its glare.

Positions: astropy's built-in ephemeris (ERFA epv00 for the Earth, plan94 for the planets: no network, no kernel
download), geocentric astrometric (light time, no aberration), as the Gaia-calibrated WCS measures them. Against
JPL Horizons' geocentric astrometric RA/Dec on 9 dates from 2025-04-15 to 2026-10-27 (tests/test_planets.py): Jupiter
and Saturn ≤ 20″, Mars and Uranus ≤ 7″, Neptune ≤ 4″. SPHEREx's low orbit moves a planet by ≤ 2″ beyond Jupiter
and ≤ 19″ for Mars at 0.52 AU: ignored, like the ephemeris error, against radii of arcminutes. Times are MJD (UTC,
MJD-AVG), taken as TT − 69.184 s, so no leap-second table is read either.

Radii (RADIUS_ARCSEC): the farthest moon SPHEREx sees (V ≲ 17; single exposures reach AB ~17.5) at its apocentre,
as seen from the planet's closest SPHEREx distance (at perihelion and 127° from the Sun, the largest solar elongation
of the 15 Nov movers; the median is 95°), and the planet's glare:
- Mars 60′: Phobos and Deimos stay within 1.1′ (0.52 AU). Mars is a mover itself, and when near it is about as
  bright in the near-infrared as Jupiter, so it gets Jupiter's glare radius. No zone has covered Mars yet.
- Jupiter 80′: Callisto ≤ 10′; Himalia (V 14.6) ≤ 71′ and Elara (V 16.6) ≤ 76′ (apocentres 13.3 and 14.2 million km
  at 4.28 AU). Seen: the Galilean moons to 8′, Himalia to 56′, Carme (V 17.9) at 66′; the unexplained movers within
  60′ are 2.7× as dense as 60–240′ away, 10 of 14 grade C, most at 40–130″/h (the moons move 6–25″/h): pairs made in
  Jupiter's glare. The outer moons Pasiphae (V 16.9), Sinope and Carme reach 2–3° and are not covered (only Carme
  was seen, inside 80′).
- Saturn 45′: Iapetus ≤ 10′, Phoebe (V 16.5) ≤ 41′ (apocentre 15.1 million km at 8.40 AU). Seen: Titan, Rhea,
  Hyperion and Iapetus to 8.5′, Phoebe to 31′; no unexplained mover within 53′.
- Uranus 5′: Oberon ≤ 46″ (17.66 AU); its irregular moons are V > 20. The rest is the glare of a V 5.7 planet (the
  pipeline finds ring pieces up to 60″ from G < 12 stars; Uranus is ~300× brighter). Seen: Uranus, Titania, Oberon,
  all within 0.8′; the nearest unexplained mover is 14.7′ away, so any radius from 1′ to 14′ gives the same list today.
- Neptune 3′: Triton ≤ 17″ (29.19 AU); Nereid (V 19.2) is too faint. Glare of a V 7.8 planet. Seen: Neptune and
  Triton within 0.3′; the nearest unexplained mover is 35′ away.
Within SELF_ARCSEC (the disc, or the rings, plus the ephemeris error) the body is the planet itself.
"""

from __future__ import annotations

import numpy as np
from astropy import units as u
from astropy.coordinates import get_body_barycentric
from astropy.time import Time

PLANETS = ("Mars", "Jupiter", "Saturn", "Uranus", "Neptune")

MARS_RADIUS_ARCMIN = 60.0
JUPITER_RADIUS_ARCMIN = 80.0
SATURN_RADIUS_ARCMIN = 45.0
URANUS_RADIUS_ARCMIN = 5.0
NEPTUNE_RADIUS_ARCMIN = 3.0
RADIUS_ARCSEC = {"Mars": MARS_RADIUS_ARCMIN * 60.0, "Jupiter": JUPITER_RADIUS_ARCMIN * 60.0,
                 "Saturn": SATURN_RADIUS_ARCMIN * 60.0, "Uranus": URANUS_RADIUS_ARCMIN * 60.0,
                 "Neptune": NEPTUNE_RADIUS_ARCMIN * 60.0}

# The planet itself: its disc (Saturn: the rings) at the closest SPHEREx distance, plus the ephemeris error (and for
# Mars SPHEREx's parallax). Farther out, within RADIUS_ARCSEC, it is one of its moons (or its glare).
SELF_ARCSEC = {"Mars": 30.0, "Jupiter": 45.0, "Saturn": 45.0, "Uranus": 15.0, "Neptune": 10.0}

TT_MINUS_UTC_S = 69.184        # 37 leap seconds (since 2017) + 32.184 s
C_AU_PER_DAY = 173.1446326846693


def _times(mjd: np.ndarray) -> Time:
    return Time(mjd + TT_MINUS_UTC_S / 86400.0, format="mjd", scale="tt")


def positions(mjd) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{planet: (ra_deg, dec_deg)}: geocentric astrometric ICRS positions at each MJD (UTC), as arrays."""
    m = np.atleast_1d(np.asarray(mjd, np.float64))
    t = _times(m)
    earth = get_body_barycentric("earth", t, ephemeris="builtin").xyz.to_value(u.au)
    out = {}
    for name in PLANETS:
        tau = np.zeros(m.shape)
        for _ in range(2):  # light time: the planet where it was when the light left it
            p = get_body_barycentric(name.lower(), t - tau * u.day, ephemeris="builtin").xyz.to_value(u.au)
            v = p - earth
            tau = np.sqrt((v ** 2).sum(axis=0)) / C_AU_PER_DAY
        x, y, z = v
        out[name] = (np.degrees(np.arctan2(y, x)) % 360.0, np.degrees(np.arctan2(z, np.hypot(x, y))))
    return out


def _unit(ra_deg, dec_deg) -> np.ndarray:
    a, d = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])


def _sep_arcsec(ra1, dec1, ra2, dec2) -> np.ndarray:
    a, b = _unit(ra1, dec1), _unit(ra2, dec2)
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(a, b, axis=0), axis=0), (a * b).sum(axis=0))) * 3600.0


def near_planets(ra_deg, dec_deg, mjd) -> tuple[np.ndarray, np.ndarray]:
    """For each position and time: (planet name, "" when none; separation from it in arcsec, NaN when none), the
    nearest planet whose RADIUS_ARCSEC holds the position. Arrays of one length (or scalars), mjd in UTC."""
    ra, dec, m = np.broadcast_arrays(np.atleast_1d(np.asarray(ra_deg, np.float64)),
                                     np.atleast_1d(np.asarray(dec_deg, np.float64)),
                                     np.atleast_1d(np.asarray(mjd, np.float64)))
    names = np.full(ra.shape, "", dtype=object)
    best = np.full(ra.shape, np.nan)
    ok = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(m)
    if not ok.any():
        return names, best
    pos = positions(m[ok])
    for name in PLANETS:
        sep = _sep_arcsec(ra[ok], dec[ok], *pos[name])
        hit = (sep <= RADIUS_ARCSEC[name]) & ~(sep >= best[ok])  # within the radius and nearer than any other
        idx = np.nonzero(ok)[0][hit]
        names[idx] = name
        best[idx] = sep[hit]
    return names, best


def near_planet(ra_deg: float, dec_deg: float, mjd: float) -> tuple[str, float] | None:
    """(planet name, separation in arcsec) when (ra, dec) lies within a planet's radius at mjd (UTC), else None."""
    names, sep = near_planets(ra_deg, dec_deg, mjd)
    return (str(names[0]), float(sep[0])) if names[0] else None


def planet_label(name: str, sep_arcsec: float) -> str:
    """What it is, in words: the planet itself within SELF_ARCSEC ("Saturn"), else "a moon of Saturn"."""
    return name if sep_arcsec <= SELF_ARCSEC[name] else f"a moon of {name}"
