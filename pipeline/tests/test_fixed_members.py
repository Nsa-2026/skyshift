"""link's measurement set (members.parquet): masked-peak dots and second dots in one photo are left out, the group
(fixed table, per-dot source_id, mover veto) is unchanged."""

import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline.link import fixed_groups, member_mask, members_table
from skyshift_pipeline.sky import offset_radec

STAR = (122.30, 16.10)
PERSIST = 1 << 17
TRANSIENT = 1 << 0
OVERFLOW = 1 << 1
SOURCE = 1 << 21


def dots_of(rows):
    d = pd.DataFrame(rows)
    d["dot_row"] = np.arange(len(d), dtype=np.int32)
    return d


def star_rows(pos, n_photos=8, flags_peak=0, start_photo=0):
    return [dict(ra=float(pos[0]), dec=float(pos[1]), session=p // 2, snr=50.0, flux_ujy=100.0,
                 flags_peak=flags_peak, photo=start_photo + p) for p in range(n_photos)]


def photos_for(n):
    return pd.DataFrame({"obs_id": [f"O{i}" for i in range(n)], "detector": [1] * n})


def test_masked_peak_dot_left_out_but_saturated_kept():
    rows = star_rows(STAR, flags_peak=SOURCE)
    off = offset_radec(*STAR, 3.0, 2.0)  # core masked: the centroid sits on the wings
    rows[3].update(ra=float(off[0]), dec=float(off[1]), flags_peak=PERSIST | SOURCE)
    rows[5].update(flags_peak=TRANSIENT)
    rows[6].update(flags_peak=OVERFLOW | TRANSIENT)  # saturated: re-centroided on the wings on purpose
    d = dots_of(rows)
    fixed, dot_sid = fixed_groups(d)
    assert len(fixed) == 1 and (dot_sid >= 0).all()  # the group keeps every dot
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep.tolist() == [True, True, True, False, True, False, True, True]
    assert st["member_dots_masked_peak"] == 2 and st["member_dots_second_in_photo"] == 0
    mem = members_table(d, dot_sid, photos_for(8), keep=keep)
    assert sorted(mem["dot_row"]) == [0, 1, 2, 4, 6, 7]


def test_source_with_every_dot_flagged_keeps_them():
    d = dots_of(star_rows(STAR, flags_peak=PERSIST))
    fixed, dot_sid = fixed_groups(d)
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep.all() and st["member_dots_masked_peak"] == 0


def test_one_dot_per_photo_nearest_to_the_group():
    rows = star_rows(STAR)
    # a wing fragment 3.5″ off in four of the star's photos: chained into the group by the 4″ link
    frag = offset_radec(*STAR, 3.5, 0.0)
    for p in (0, 2, 4, 6):
        rows.append(dict(ra=float(frag[0]), dec=float(frag[1]), session=p // 2, snr=8.0, flux_ujy=5.0,
                         flags_peak=0, photo=p))
    d = dots_of(rows)
    fixed, dot_sid = fixed_groups(d)
    assert len(fixed) == 1 and (dot_sid >= 0).all()
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep[:8].all() and not keep[8:].any()
    assert st["member_dots_second_in_photo"] == 4
    mem = members_table(d, dot_sid, photos_for(8), keep=keep)
    assert len(mem) == 8 and not mem.duplicated(["source_id", "obs_id", "detector"]).any()


def test_switches_off_and_default_members_unchanged(monkeypatch):
    rows = star_rows(STAR)
    rows[2]["flags_peak"] = PERSIST
    frag = offset_radec(*STAR, 3.5, 0.0)
    rows.append(dict(ra=float(frag[0]), dec=float(frag[1]), session=0, snr=8.0, flux_ujy=5.0, flags_peak=0, photo=0))
    d = dots_of(rows)
    fixed, dot_sid = fixed_groups(d)
    assert len(members_table(d, dot_sid, photos_for(8))) == 9  # no keep: every dot of the group, as before
    monkeypatch.setattr(C, "FIXED_MEMBER_DROP_MASKED_PEAK", False)
    monkeypatch.setattr(C, "FIXED_MEMBER_ONE_PER_PHOTO", False)
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep.all() and st == {"member_dots_masked_peak": 0, "member_dots_second_in_photo": 0}


def test_two_sources_one_photo_each_keep_their_own_dot():
    a, b = STAR, offset_radec(*STAR, 30.0, 0.0)
    rows = star_rows(a) + star_rows(b)  # both in the same photos: one dot each per photo, nothing dropped
    d = dots_of(rows)
    fixed, dot_sid = fixed_groups(d)
    assert len(fixed) == 2
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep.all() and st["member_dots_second_in_photo"] == 0


def test_empty():
    d = dots_of([]).assign(ra=[], dec=[], session=[], snr=[], flux_ujy=[], flags_peak=[], photo=[])
    fixed, dot_sid = fixed_groups(d)
    keep, st = member_mask(d, dot_sid, fixed)
    assert keep.size == 0 and st["member_dots_masked_peak"] == 0
