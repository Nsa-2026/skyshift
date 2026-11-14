import numpy as np

from skyshift_core import flags as F


def test_constants_match_contract_bits():
    mask = (F.TRANSIENT | F.OVERFLOW | F.SUR_ERROR | F.NONFUNC | F.DICHROIC | F.MISSING_DATA | F.HOT
            | F.COLD | F.PHANMISS | F.NONLINEAR | F.PERSIST | F.STREAK | F.SNOWBALL)
    record = F.OUTLIER | F.CROSSTALK | F.GHOST | F.GHOST_FPA | F.GHOST_EXT | F.BLOOM | F.HALO
    assert mask == F.MASK_BITS == 0x0A02CEC7
    assert record == F.RECORD_BITS == 0x15D80000
    assert F.FULLSAMPLE | F.SOURCE == F.NEVER_BITS == 0x00201000
    assert F.OVERFLOW | F.NONLINEAR == F.SAT_BITS == 0x00008002
    assert F.MASK_BITS & F.RECORD_BITS == 0
    assert F.MASK_BITS & F.NEVER_BITS == 0
    assert F.RECORD_BITS & F.NEVER_BITS == 0
    assert F.SOURCE == 1 << 21 and F.FULLSAMPLE == 1 << 12


def test_source_and_fullsample_never_masked():
    f = np.array([F.SOURCE, F.FULLSAMPLE, F.SOURCE | F.FULLSAMPLE, 0], dtype=np.int32)
    assert not F.bad_pixel_mask(f).any()
    # record bits are never masked either
    assert not F.bad_pixel_mask(np.array([F.RECORD_BITS, F.HALO | F.SOURCE])).any()


def test_each_mask_bit_masks_even_with_source():
    for bit in range(32):
        v = (1 << bit) | F.SOURCE
        expected = bool((1 << bit) & F.MASK_BITS)
        assert bool(F.bad_pixel_mask(np.array([v], dtype=np.int64))[0]) == expected, bit


def test_record_bits_keep_only_record():
    f = np.array([F.SOURCE | F.HALO | F.TRANSIENT, F.CROSSTALK | F.OVERFLOW], dtype=np.int32)
    np.testing.assert_array_equal(F.record_bits(f), [F.HALO, F.CROSSTALK])


def test_saturated_core():
    fl = np.full((21, 21), F.SOURCE, dtype=np.int32)
    fl[10, 10] |= F.NONLINEAR
    assert F.is_saturated_core(fl, 10.2, 9.8)
    assert not F.is_saturated_core(fl, 15, 15)
    fl[3, 3] |= F.OVERFLOW
    assert F.is_saturated_core(fl, 3.9, 3.0, r=1.0)
    assert not F.is_saturated_core(fl, 6, 6)
    # other mask bits do not mean saturated
    fl2 = np.full((5, 5), F.TRANSIENT | F.HOT, dtype=np.int32)
    assert not F.is_saturated_core(fl2, 2, 2)


def test_flags_under_and_fracs():
    fl = np.zeros((10, 10), dtype=np.int32)
    fl[5, 5] = F.HALO
    fl[5, 6] = F.TRANSIENT
    assert F.flags_under(fl, 5, 5, 1.0) == F.HALO | F.TRANSIENT
    assert F.flags_under(fl, 0, 0, 1.0) == 0
    assert F.flag_frac(fl) == 0.01
    fl[0, 0] = F.OVERFLOW
    assert F.sat_frac(fl) == 0.01
    assert F.bit_names(F.SOURCE | F.HALO) == ["SOURCE", "HALO"]


def test_fixture_flags(cutout_path):
    from skyshift_core import open_level2

    l2 = open_level2(cutout_path)
    m = F.bad_pixel_mask(l2.flags)
    src = (l2.flags & F.SOURCE) != 0
    assert src.mean() > 0.9  # SOURCE is nearly everywhere ...
    assert m.mean() < 0.2  # ... and is not masked
