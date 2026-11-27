"""SKYSHIFT_PARQUET_SLIM (config PARQUET_SLIM, off by default): the photo worker's files in fewer bytes, the same
tables. Every file type the worker writes, read back from a slim file, equals the default file's table bit for bit:
schema, metadata, values (NaN payloads too), row order."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import skyshift_pipeline.config as C
from skyshift_pipeline import coadd as CO
from skyshift_pipeline import dots as D
from skyshift_pipeline import parquet_rows as PR
from skyshift_pipeline import streaks as SK

from test_stack_fused import OBS, V2, session  # noqa: F401  (the fixture: three exposures, D1 + D4, fused stacks)


def _same_array(a, b) -> bool:
    a = a.combine_chunks() if isinstance(a, pa.ChunkedArray) else a
    b = b.combine_chunks() if isinstance(b, pa.ChunkedArray) else b
    if a.type != b.type or len(a) != len(b) or a.null_count != b.null_count:
        return False
    if pa.types.is_fixed_size_list(a.type) or pa.types.is_list(a.type):
        return a.is_null().equals(b.is_null()) and _same_array(a.flatten(), b.flatten())
    if pa.types.is_floating(a.type):
        x, y = a.to_numpy(zero_copy_only=False), b.to_numpy(zero_copy_only=False)
        u = {2: np.uint16, 4: np.uint32, 8: np.uint64}[x.dtype.itemsize]
        return a.is_null().equals(b.is_null()) and np.array_equal(x.view(u), y.view(u))
    return a.equals(b)


def assert_identical(a: pa.Table, b: pa.Table) -> None:
    assert a.schema.equals(b.schema, check_metadata=True)
    assert a.num_rows == b.num_rows
    for name in a.column_names:
        assert _same_array(a.column(name), b.column(name)), name


def _read(raw: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(raw))


def _encodings(raw: bytes) -> dict[str, set[str]]:
    md = pq.ParquetFile(io.BytesIO(raw)).metadata
    return {md.row_group(0).column(i).path_in_schema: set(md.row_group(0).column(i).encodings)
            for i in range(md.num_columns)}


def _forced_table(n: int = 300) -> pa.Table:
    rng = np.random.default_rng(1)
    df = pd.DataFrame({c: [""] * n if str(t) in ("str", "string", "object") else np.zeros(n, t)
                       for c, t in D.FORCED_COLUMNS.items()}).astype(D.FORCED_COLUMNS)
    for c, t in D.FORCED_COLUMNS.items():
        if str(t).startswith("float"):
            v = rng.normal(100, 30, n).astype(t)
            v[::17] = np.nan
            df[c] = v
    df["obs_id"] = "2025W18_1B_0074_2"
    df["ref_id"] = [f"gaia:{4_000_000_000_000 + 7 * i}" if i % 5 else f"allwise:J{i:06d}+000000.0" for i in range(n)]
    df["flags"] = rng.integers(0, 1 << 20, n).astype("int32")
    return pa.Table.from_pandas(df, preserve_index=False)


def test_slim_is_off_unless_the_env_switch_is_set():
    import os
    assert C.PARQUET_SLIM == (os.environ.get("SKYSHIFT_PARQUET_SLIM", "0") == "1")


def test_slim_maps_load_identical_with_np_load():
    rng = np.random.default_rng(5)
    flux = rng.normal(0, 1e-3, (300, 280)).astype(np.float16)
    err = (1e-3 + 1e-4 * np.add.outer(np.arange(300), np.arange(280)) / 300).astype(np.float16)
    flux[:20] = 0
    flux[5, 5] = np.nan
    mp = {"flux": flux, "err": err, "ncov": rng.integers(0, 5, (300, 280)).astype(np.int8),
          "header": np.array("SIMPLE  =                    T" * 30), "x0": np.int64(12), "y0": np.int64(-3),
          "conv": np.float64(889.0), "sigma_norm": np.float64(1.07)}
    a, b = CO.npz_bytes(mp, slim=False), CO.npz_bytes(mp, slim=True)
    with np.load(io.BytesIO(a)) as za, np.load(io.BytesIO(b)) as zb:
        assert sorted(za.files) == sorted(zb.files) == sorted(mp)
        for k in mp:
            x, y = za[k], zb[k]
            assert x.dtype == y.dtype and x.shape == y.shape
            assert x.tobytes() == y.tobytes(), k


def test_every_file_of_the_fused_worker_reads_back_identical_when_rewritten_slim(tmp_path, session):  # noqa: F811
    photos, tasks, load, reads = session
    root = tmp_path / "default"
    st = D.process_photos(str(root), photos, prefetch=False, stacks=tasks)
    assert all(s["status"] == "done" for s in st)
    files = []
    for o in OBS:
        for det in (1, 4):
            files += [root / D.dots_rel(o, det, V2.folder), root / D.peaks_rel(o, det, V2.folder),
                      root / D.ext_rel(o, det, V2.folder), root / SK.streaks_rel(o, det, V2.folder)]
    for t in tasks:
        files += [root / CO.session_rel(t["key"]), root / CO.session_rel(t["key"], photos=True),
                  root / CO.session_moving_rel(t["key"])]
    assert all(f.exists() for f in files)
    total_now = total_slim = 0
    for f in files:
        raw = f.read_bytes()
        default = _read(raw)
        slim = D.parquet_bytes(default, slim=True)
        assert_identical(default, _read(slim))
        assert D.photo_meta(slim) == D.photo_meta(raw)
        total_now += len(raw)
        total_slim += len(slim)
    assert total_slim < total_now


def test_the_switch_writes_slim_files_with_the_same_tables(tmp_path, session, monkeypatch):  # noqa: F811
    photos, tasks, load, reads = session
    a, b = tmp_path / "default", tmp_path / "slim"
    D.process_photos(str(a), photos, prefetch=False, stacks=tasks)
    monkeypatch.setattr(C, "PARQUET_SLIM", True)
    reads.clear()
    D.process_photos(str(b), photos, prefetch=False, stacks=tasks)
    for o in OBS:
        for det in (1, 4):
            for rel in (D.dots_rel(o, det, V2.folder), D.peaks_rel(o, det, V2.folder), D.ext_rel(o, det, V2.folder),
                        SK.streaks_rel(o, det, V2.folder)):
                ta, tb = pq.read_table(a / rel), pq.read_table(b / rel)
                # the dot footer holds CPU times: compare it without them; every value and type must be the same
                assert ta.schema.remove_metadata().equals(tb.schema.remove_metadata())
                for name in ta.column_names:
                    assert _same_array(ta.column(name), tb.column(name)), (rel, name)
            enc = _encodings((b / D.dots_rel(o, det, V2.folder)).read_bytes())
            assert "BYTE_STREAM_SPLIT" in enc["flux_ap_ujy.list.element"]
            assert "BYTE_STREAM_SPLIT" in enc["flux_ap_err_ujy.list.element"]
    for t in tasks:
        for rel in (CO.session_rel(t["key"]), CO.session_rel(t["key"], photos=True), CO.session_moving_rel(t["key"])):
            ta, tb = pq.read_table(a / rel), pq.read_table(b / rel)
            assert ta.schema.remove_metadata().equals(tb.schema.remove_metadata())
            pd.testing.assert_frame_equal(ta.to_pandas(), tb.to_pandas())
        enc = _encodings((b / CO.session_rel(t["key"])).read_bytes())  # stack_row = 0, 1, 2, ...
        assert "DELTA_BINARY_PACKED" in enc["stack_row"] and "BYTE_STREAM_SPLIT" in enc["flux_ujy"]
        assert "BYTE_STREAM_SPLIT" in _encodings((b / CO.session_rel(t["key"], photos=True)).read_bytes())["chi2_nu"]


def test_forced_ref_ids_unique_strings_skip_the_dictionary_and_read_back_identical():
    t = _forced_table()
    now, slim = D.parquet_bytes(t, slim=False), D.parquet_bytes(t, slim=True)
    assert_identical(t, _read(slim))
    assert_identical(_read(now), _read(slim))
    enc = _encodings(slim)
    assert not any("DICTIONARY" in e for e in enc["ref_id"])  # unique per row
    assert any("DICTIONARY" in e for e in enc["obs_id"])      # one value: dictionary
    assert "BYTE_STREAM_SPLIT" in enc["ra"]


def test_a_decreasing_or_null_integer_column_is_not_delta_coded_and_empty_tables_work():
    t = pa.table({"stack_row": pa.array([0, 1, 2, 3], pa.int32()), "flags": pa.array([5, 1, 9, 0], pa.int32()),
                  "n": pa.array([1, None, 3, 4], pa.int16()), "f": pa.array([1.5, np.nan, 2.0, 3.0], pa.float32())})
    raw = D.parquet_bytes(t, slim=True)
    assert_identical(t, _read(raw))
    enc = _encodings(raw)
    assert "DELTA_BINARY_PACKED" in enc["stack_row"]
    assert "DELTA_BINARY_PACKED" not in enc["flags"] and "DELTA_BINARY_PACKED" not in enc["n"]
    e = t.slice(0, 0)
    assert_identical(e, _read(D.parquet_bytes(e, slim=True)))


def test_dot_table_with_lists_and_photo_metadata_and_the_range_reader(tmp_path):
    rng = np.random.default_rng(3)
    n = 500
    df = D.empty_dots(True)
    df = pd.DataFrame({c: [""] * n if df[c].dtype.kind not in "biuf" else np.zeros(n, df[c].dtype)
                       for c in df.columns if c not in D.DOT_LIST_COLUMNS}).astype(
        {c: df[c].dtype for c in df.columns if c not in D.DOT_LIST_COLUMNS})
    for c in df.columns:
        if getattr(df[c].dtype, "kind", "") == "f":
            v = rng.normal(10, 3, n).astype(df[c].dtype)
            v[::23] = np.nan
            df[c] = v
    df["obs_id"] = "X_1"
    for c in D.DOT_LIST_COLUMNS:
        df[c] = list(rng.normal(5, 1, (n, len(C.AP_RADII_PX))).astype(np.float32))
    t = D.dots_arrow(df, {"obs_id": "X_1", "detector": 2, "n": 3})
    raw = D.parquet_bytes(t, slim=True)
    assert_identical(_read(D.parquet_bytes(t, slim=False)), _read(raw))  # (in memory the list child is `item`)
    assert D.photo_meta(raw) == D.photo_meta(D.parquet_bytes(t, slim=False))
    p = tmp_path / "d.parquet"
    p.write_bytes(raw)
    import fsspec
    cols = ["x", "flags", "snr"]
    got = PR.read_table(fsspec.filesystem("file"), str(p), cols)
    assert_identical(pq.read_table(p, columns=cols).replace_schema_metadata(None), got.replace_schema_metadata(None))


@pytest.mark.parametrize("level", [3, 15])
def test_the_level_changes_bytes_not_values(monkeypatch, level):
    monkeypatch.setattr(C, "PARQUET_SLIM_LEVEL", level)
    t = _forced_table(2000)
    assert_identical(t, _read(D.parquet_bytes(t, slim=True)))
