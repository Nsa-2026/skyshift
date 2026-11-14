import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skyshift_server import known, resolve as resolve_mod
from skyshift_server.errors import ApiError
from skyshift_server.resolve import MOVING, builtins, parse_coords, position_now, resolve

WISE_RA, WISE_DEC = 133.7951292, -7.2451472  # 08h55m10.831s -7d14m42.53s
REPO = Path(__file__).resolve().parents[2]
# kete's MPC positions at MOVING's times (14 Nov index, run by hand): 1-3" from the frame centres
MPC_AT_FRAMES = {"3I/ATLAS": (166.97415159, 5.22794356), "Elpis": (88.71172217, 14.48579347),
                 "Euphrosyne": (210.13915365, -24.84641866)}


@pytest.fixture(autouse=True)
def fake_mpc(monkeypatch):
    """known.position needs the MPC index and kete's kernels (not in tests): canned answers."""
    calls = []

    def position(name, mjd, *, cache=None):
        calls.append((name, mjd))
        return MPC_AT_FRAMES[name]

    monkeypatch.setattr(known, "position", position)
    resolve_mod.moving_position.cache_clear()
    yield calls
    resolve_mod.moving_position.cache_clear()


@pytest.mark.parametrize("q", [
    "08h55m10.831s -7d14m42.53s",
    "08h55m10.831s, -7d14m42.53s",
    "08h 55m 10.831s -07d 14m 42.53s",
    "8h55m10.831s −7°14′42.53″",
    "08:55:10.831 -07:14:42.53",
    "08:55:10.831, -07:14:42.53",
    "08 55 10.831 -07 14 42.53",
    "08 55 10.831 - 07 14 42.53",
    "  08:55:10.831   −7:14:42.53  ",
    "133d47m42.465s -7d14m42.53s",
    "133°47'42.465\" -7°14'42.53\"",
    "133.7951292 -7.2451472",
    "133.7951292,-7.2451472",
    "133.7951292, -7.2451472",
    "133.7951292° -7.2451472°",
    "133.7951292d-7.2451472d",
    "133.7951292 deg -7.2451472 deg",
    "J085510.831-071442.53",
    "j085510.831-071442.53",
])
def test_coordinate_formats(q):
    r = resolve(q)
    assert r["source"] == "coords" and r["name"] is None and r["slug"] is None and r["q"] == q.strip()
    assert r["ra"] == pytest.approx(WISE_RA, abs=2e-6)
    assert r["dec"] == pytest.approx(WISE_DEC, abs=2e-6)


@pytest.mark.parametrize("q,ra,dec", [
    ("0 0", 0.0, 0.0),
    ("360 0", 0.0, 0.0),
    ("359.9, -89.9", 359.9, -89.9),
    ("12 30 00 +45 30 00", 187.5, 45.5),
    ("12 30 00 45 30 00", 187.5, 45.5),        # no sign: halves
    ("12:30 +45:30", 187.5, 45.5),              # hh:mm dd:mm
    ("12h30m +45d30m", 187.5, 45.5),
    ("12.5h 45.5", 187.5, 45.5),
    ("23 59 59.99 -00 00 30", 359.99995833, -1 / 120),  # negative zero degrees keeps its sign
    ("10.5 +5", 10.5, 5.0),
    ("10.5 ＋5", 10.5, 5.0),
    ("10.5 –5", 10.5, -5.0),               # en dash as minus
    ("166.8 5.3", 166.8, 5.3),
    ("+12 +10", 12.0, 10.0),
])
def test_coordinate_values(q, ra, dec):
    r = resolve(q)
    assert r["ra"] == pytest.approx(ra, abs=1e-6)
    assert r["dec"] == pytest.approx(dec, abs=1e-6)


@pytest.mark.parametrize("q", [
    "400 0", "-10 5", "10 95", "10 -91", "25:00:00 +10:00:00", "12 61 00 +10 00 00",
    "12 30 60 +10 00 00", "1 2 3", "12", "12.5", ",", "1,2,3", "12 30 00 +45 30 00 5",
    "12.5:30 +10", "-12 +10", "12h 10h", "12 30 00 +10d 5h",
])
def test_bad_coordinates_are_400(q):
    with pytest.raises(ApiError) as e:
        resolve(q)
    assert e.value.status == 400 and e.value.code == "bad_request"


@pytest.mark.parametrize("q", ["", "   "])
def test_empty_is_400(q):
    with pytest.raises(ApiError) as e:
        resolve(q)
    assert e.value.status == 400


@pytest.mark.parametrize("q", ["Proxima Centauri", "foo", "M31", "Vega", "nan nan", "1e5 3"])
def test_unknown_name_is_404(q):
    with pytest.raises(ApiError) as e:
        resolve(q)
    assert e.value.status == 404 and e.value.code == "not_found"


@pytest.mark.parametrize("q,slug", [
    ("barnard", "barnard"), ("Barnard", "barnard"), ("Barnard's Star", "barnard"),
    ("barnards star", "barnard"), ("BARNARD’S STAR", "barnard"), ("GJ 699", "barnard"),
    ("Epsilon Indi B", "epsindib"), ("eps Indi B", "epsindib"), ("ε Indi B", "epsindib"),
    ("epsindib", "epsindib"),
    ("WISE 0855", "wise0855"), ("WISE 0855−0714", "wise0855"), ("WISE 0855-0714", "wise0855"),
    ("wise0855", "wise0855"), ("WISE J085510.83-071442.5", "wise0855"),
    ("WISE J085510.83−071442.5", "wise0855"),
    ("RW Aurigae", "rwaur"), ("RW Aur", "rwaur"), ("rw aur", "rwaur"),
    ("R Draconis", "rdra"), ("R Dra", "rdra"),
    ("3I/ATLAS", "3i-atlas"), ("3I", "3i-atlas"), ("3i atlas", "3i-atlas"),
    ("C/2025 N1", "3i-atlas"), ("C/2025 N1 (ATLAS)", "3i-atlas"), ("3i-atlas", "3i-atlas"),
    ("Comet 3I/ATLAS", "3i-atlas"),
    ("Elpis", "elpis"), ("(59) Elpis", "elpis"), ("59 Elpis", "elpis"), ("elpis", "elpis"),
    ("Euphrosyne", "euphrosyne"), ("(31) Euphrosyne", "euphrosyne"), ("31 euphrosyne", "euphrosyne"),
])
def test_builtin_names(q, slug):
    r = resolve(q)
    assert r["source"] == "builtin" and r["slug"] == slug and r["q"] == q
    assert r["name"] == builtins()[slug]["name"]
    assert 0 <= r["ra"] < 360 and -90 <= r["dec"] <= 90


def test_builtins_cover_targets_and_comet():
    assert set(builtins()) == {"barnard", "epsindib", "wise0855", "rwaur", "rdra", "3i-atlas", "elpis", "euphrosyne"}


def test_every_featured_chip_resolves():
    """14 Nov site review: /api/resolve gave 404 for Elpis and Euphrosyne, two of the site's chips."""
    chips = json.loads((REPO / "web/src/data/targets.json").read_text())["targets"]
    assert {t["slug"] for t in chips} <= set(builtins())
    for t in chips:
        r = resolve(t["name"])
        assert r["slug"] == t["slug"] and r["source"] == "builtin"


def test_barnard_moved_to_present():
    # contract fixture resolve_barnard.json is Barnard's Star at ~2026.87
    when = datetime(2026, 11, 14, tzinfo=timezone.utc)
    r = resolve("Barnard's Star", now=when)
    assert r["dec"] == pytest.approx(4.7707066, abs=1e-5)  # +10.36"/yr x 10.87 yr north
    assert r["ra"] == pytest.approx(269.4460741, abs=1e-5)
    t = builtins()["barnard"]
    ra0, dec0 = position_now(t, datetime(2016, 1, 1, 12, tzinfo=timezone.utc))
    assert dec0 == pytest.approx(t["dec"], abs=1e-6)


def test_moving_targets_are_where_their_frames_are_from_mpc_orbits(fake_mpc):
    """3I/ATLAS, Elpis and Euphrosyne: the MPC position (known.position, kete) at the median exposure
    time of their featured frames, which is where the frames are centred. Before: the comet came
    from NASA's list, 9.4' from the frame centre, and the asteroids were unknown names (404)."""
    for slug, name in (("3i-atlas", "3I/ATLAS"), ("elpis", "Elpis"), ("euphrosyne", "Euphrosyne")):
        r = resolve(slug)
        assert (r["name"], r["slug"], r["source"]) == (MOVING[slug]["name"], slug, "builtin")
        assert (r["ra"], r["dec"]) == pytest.approx(MPC_AT_FRAMES[name], abs=1e-6)
        assert fake_mpc[-1] == (name, MOVING[slug]["mjd"])
        frames = json.loads((REPO / f"web/public/data/{slug}/frames.json").read_text())
        assert MOVING[slug]["mjd"] == pytest.approx(statistics.median(e["mjd_mid"] for e in frames["epochs"]), abs=1e-5)
        centre = json.loads((REPO / f"web/public/data/{slug}/index.json").read_text())["center"]
        d = 3600 * math.hypot((r["ra"] - centre["ra"]) * math.cos(math.radians(centre["dec"])), r["dec"] - centre["dec"])
        assert d < 5.0  # arcsec (kete vs NASA's per-image positions: 1-3")
    n = len(fake_mpc)
    resolve("Elpis")
    assert len(fake_mpc) == n  # computed once per instance


def test_moving_target_without_mpc_is_502_not_a_guess(monkeypatch):
    def broken(name, mjd, *, cache=None):
        raise KeyError(name)

    monkeypatch.setattr(known, "position", broken)
    with pytest.raises(ApiError) as e:
        resolve("Euphrosyne")
    assert e.value.status == 502 and e.value.code == "upstream"
    assert resolve("Barnard's Star")["slug"] == "barnard"  # the stars don't need the index


def test_matches_fixture_coords():
    import json
    from pathlib import Path

    fx = json.loads((Path(__file__).resolve().parents[2] / "fixtures/api/resolve_coords.json").read_text())
    r = resolve(fx["q"])
    for k in ("q", "name", "slug", "source"):
        assert r[k] == fx[k]
    assert r["ra"] == pytest.approx(fx["ra"], abs=1e-6) and r["dec"] == pytest.approx(fx["dec"], abs=1e-6)


def test_parse_coords_returns_degrees():
    assert parse_coords("00h00m00s +00d00m00s") == (0.0, 0.0)
