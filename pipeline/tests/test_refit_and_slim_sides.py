"""27 Nov: the bounded wings fit of refused matched-filter dots (dots.refit_refused_skip, dot_flag 0x80) and the slim
switch on the conditions and stamp writers (same values, only compression settings change)."""
import io
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from skyshift_pipeline import config as C
from skyshift_pipeline import cond as CD
from skyshift_pipeline import dots as D
from skyshift_pipeline import stamps as SP


def _ctx(shape=(40, 40)):
    return SimpleNamespace(bad=np.zeros(shape, bool), bkg_level=np.zeros(shape), noise0=np.ones(shape))


def test_refit_rule_floor_cap_brightest_first_and_only_refused_saturated_dots(monkeypatch):
    snr = np.array([3.0, 8.0, 20.0, 12.0, 9.0, 50.0])
    monkeypatch.setattr(D, "mf_at", lambda sub, noise, bad, x, y, epsf: snr[np.asarray(x, int)])
    l2 = SimpleNamespace(data=np.zeros((40, 40)), epsf=object())
    x = np.arange(6, dtype=float)
    y = np.zeros(6)
    refused = C.dot_flag_mem(C.DOT_FLAG_MF_MIRROR)
    mem = np.array([refused, refused, refused, refused, refused, 0], np.int64)  # the last one is not refused
    fit = np.ones(6, bool)
    skip = D.refit_refused_skip(l2, _ctx(), x, y, mem, fit, min_snr=7.0, cap=-1)
    assert skip.tolist() == [True, False, False, False, False, False]  # S/N 3 < 7; the unrefused dot is never skipped
    skip = D.refit_refused_skip(l2, _ctx(), x, y, mem, fit, min_snr=7.0, cap=2)
    assert skip.tolist() == [True, True, False, False, True, False]  # 20 and 12 kept (brightest), 8 and 9 over the cap
    assert not D.refit_refused_skip(l2, _ctx(), x, y, mem, fit, min_snr=0.0, cap=-1).any()  # the old behaviour
    assert not D.refit_refused_skip(l2, _ctx(), x, y, mem, np.zeros(6, bool), min_snr=7.0, cap=0).any()  # not fitted
    assert C.DOT_FLAGS["pos_norefit"] == C.DOT_FLAG_POS_NOREFIT == 0x80


def _cond_table():
    rng = np.random.default_rng(1)
    n = 256
    t = pa.table({"tile": pa.array(np.arange(n, dtype=np.int16)),
                  "ra": pa.array(rng.normal(size=n).astype(np.float32)),
                  "bkg_mjysr": pa.array(rng.normal(size=n).astype(np.float16)),
                  "n_dots": pa.array(rng.integers(0, 500, n).astype(np.uint16))})
    return t.replace_schema_metadata({CD.META_KEY: '{"version": 1}'})


def _stamp_table():
    rng = np.random.default_rng(2)
    rows = []
    for k in range(3):
        r = {}
        for c, t in SP.ROW_COLUMNS.items():
            r[c] = f"s{k}" if t == "str" else np.dtype(t).type(rng.integers(0, 100))
        for p in SP.PLANES:
            r[p] = rng.integers(0, 255, 400, dtype=np.uint8).tobytes()
        rows.append(r)
    return SP.table(rows, {"version": 1, "obs_id": "OBS"})


def test_slim_switch_on_cond_and_stamp_files_keeps_every_value(monkeypatch):
    for make, write in ((_cond_table, CD.table_bytes), (_stamp_table, SP.parquet_bytes)):
        t = make()
        monkeypatch.setattr(C, "PARQUET_SLIM", False)
        off = write(t)
        monkeypatch.setattr(C, "PARQUET_SLIM", True)
        on = write(t)
        a, b = pq.read_table(io.BytesIO(off)), pq.read_table(io.BytesIO(on))
        assert a.equals(b) and a.schema.metadata == b.schema.metadata
        meta_on = pq.ParquetFile(io.BytesIO(on)).metadata
        assert meta_on.row_group(0).column(0).compression == "ZSTD"
