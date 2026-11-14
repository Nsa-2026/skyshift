import numpy as np
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.sky import (Footprint, SkyTree, angsep, cell_of, neighbour_cells, offset_radec,
                                   parse_polygon, tile_of, zone_tiles)

S_REGION = ("POLYGON ICRS 122.95841479319971 13.183534969834449 119.44737670053233 13.841104799139334 "
            "120.22816802854675 17.2435508884762 123.82521817133913 16.572008077517623 "
            "122.95841479319971 13.183534969834449")


def test_cell_formula_matches_contract_example():
    # fixtures/api/suspects.json SYNTH-S1 and SYNTH-S2 (cells written with the contracts/dots.md formula)
    assert int(cell_of(125.45238, 5.559772)) == 573350074917
    assert int(cell_of(125.189902, 5.8039)) == 574820074728
    band = int(np.floor((20.0 + 90) * 600))
    col = int(np.floor(125.0 * np.cos(np.deg2rad((band + 0.5) / 600 - 90)) * 600))
    assert int(cell_of(125.0, 20.0)) == band * 10_000_000 + col


def test_neighbour_cells_cover_an_edge():
    ra, dec = 121.3, 15.7
    nb = neighbour_cells([ra], [dec])[0]
    assert int(cell_of(ra, dec)) in nb
    # a point 5″ away in any direction lands in one of the 9 cells
    for ang in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        r2, d2 = offset_radec(ra, dec, 5 * np.sin(ang), 5 * np.cos(ang))
        assert int(cell_of(r2, d2)) in nb


def test_zone_tiles():
    tid, ra, dec = zone_tiles(C.ZONES["hunt"])
    assert tid.size == 40 * 40
    tid_p, _, _ = zone_tiles(C.ZONES["pilot"])
    assert tid_p.size == 14 * 14
    assert set(tid_p) <= set(tid)  # one grid: pilot tiles are hunt tiles
    assert int(tile_of(120.6, 14.5)) == 0
    assert int(tile_of(120.6 + 0.25, 14.5 + 0.25)) == 10_001
    assert int(tile_of(119.0, 15.0, C.ZONES["pilot"])) == -1
    assert int(tile_of(125.0, 15.0, C.ZONES["pilot"])) == -1
    assert int(tile_of(125.0, 15.0, C.ZONES["hunt"])) >= 0


def test_footprint():
    v = parse_polygon(S_REGION)
    assert v.shape == (4, 2)
    fp = Footprint(v)
    assert fp.contains([121.6], [15.2])[0]
    assert not fp.contains([125.0], [15.2])[0]
    assert 2.3 < fp.radius_deg() < 2.6
    d = fp.edge_distance_deg([121.6, 125.0], [15.2, 15.2])
    assert d[0] > 1.5 and d[1] < 0
    assert fp.contains(v[:, 0] * 0.999 + fp.ra0 * 0.001, v[:, 1] * 0.999 + fp.dec0 * 0.001).all()


def test_skytree_and_offsets():
    r, d = offset_radec(121.0, 15.0, 10.0, 0.0)
    assert angsep(121.0, 15.0, r, d) * 3600 == pytest.approx(10.0, rel=1e-3)
    tree = SkyTree([121.0, 121.1], [15.0, 15.0])
    sep, i = tree.nearest([r], [d])
    assert i[0] == 0 and sep[0] == pytest.approx(10.0, rel=1e-3)
    assert tree.query_radius(r, d, 12.0)[0] == [0]
    assert SkyTree([], []).nearest([1.0], [1.0])[1][0] == -1
