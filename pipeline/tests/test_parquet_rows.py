"""parquet_rows: columns by exact ranges (read_table) and some rows by pages (read_rows) equal pyarrow's full read."""

import json

import fsspec
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyshift_pipeline import parquet_rows as PR


def _dots(tmp_path, n=20000, seed=3):
    """A dot-file-like parquet: numbers, a string and a list column, and ~60 KB of key-value metadata (a dot file's
    footer is ~63 KB: ARROW:schema with the photo's facts, skyshift_photo, pandas)."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"obs_id": "2025W43_2A_0405_1", "detector": np.int8(4), "mjd": 60958.4 + rng.random(n) / 1e3,
                       "ra": rng.random(n) * 360, "dec": rng.random(n) * 10, "snr": rng.random(n).astype(np.float32),
                       "pos_err_arcsec": rng.random(n).astype(np.float32),
                       "flags": rng.integers(0, 2**31 - 1, n, dtype=np.int32), "cell": rng.integers(0, 10**12, n)})
    t = pa.Table.from_pandas(df, preserve_index=False)
    t = t.append_column("flux_ap_ujy", pa.array([[1.0] * 5] * n, pa.list_(pa.float32(), 5)))
    big = json.dumps({"facts": [f"value {i} " * 4 for i in range(1500)]})
    t = t.replace_schema_metadata({**(t.schema.metadata or {}), b"skyshift_photo": big.encode()})
    p = tmp_path / "dots.parquet"
    pq.write_table(t, p, compression="zstd")
    return str(p)


class _Counting:
    """A local filesystem that counts the bytes and requests of every read."""

    def __init__(self):
        self.fs, self.bytes, self.requests = fsspec.filesystem("file"), 0, 0

    def size(self, path):
        self.requests += 1
        return self.fs.size(path)

    def cat_file(self, path, start=None, end=None):
        b = self.fs.cat_file(path, start=start, end=end)
        self.bytes += len(b)
        self.requests += 1
        return b


def test_read_table_numbers_skip_the_footer_metadata(tmp_path, monkeypatch):
    """Plain numeric columns: only the footer's start, then one request per chunk; the same table as pyarrow."""
    path = _dots(tmp_path)
    md = pq.read_metadata(path)
    assert md.serialized_size > 50_000
    for cols in (["flags"], ["ra", "dec", "mjd", "snr", "pos_err_arcsec"], ["flags", "nope", "detector"]):
        fs = _Counting()
        t = PR.read_table(fs, path, cols)
        exp = pq.ParquetFile(path).read(columns=[c for c in cols if c in md.schema.names])
        assert t.equals(exp) and t.schema.types == exp.schema.types
        pd.testing.assert_frame_equal(t.to_pandas(), exp.to_pandas())
        chunks = sum(md.row_group(0).column(md.schema.names.index(c)).total_compressed_size
                     for c in cols if c in md.schema.names)
        assert fs.bytes <= chunks + 8 + PR.PREFIX_BYTES < chunks + md.serialized_size     # not the whole footer
        assert fs.requests <= 3 + len(t.column_names)   # size, last 8 bytes, footer start, ≤ one per chunk (coalesced)
    assert PR.Footer(_Counting(), path, ["flags"]).full is False
    # a footer start shorter than the schema and row groups: read on in bigger steps
    monkeypatch.setattr(PR, "PREFIX_BYTES", 64)
    assert PR.read_table(_Counting(), path, ["flags"]).equals(pq.ParquetFile(path).read(columns=["flags"]))


def test_read_table_other_types_read_the_whole_footer(tmp_path):
    """A string, dictionary or list column needs the Arrow schema in the key-value metadata: the whole footer."""
    path = _dots(tmp_path)
    for cols in (["obs_id", "flags"], ["flux_ap_ujy"], ["cell", "detector", "obs_id"]):
        fs = _Counting()
        t = PR.read_table(fs, path, cols)
        exp = pq.ParquetFile(path).read(columns=cols)
        assert t.equals(exp) and t.schema.types == exp.schema.types
        assert fs.bytes > pq.read_metadata(path).serialized_size
    m_path, _ = _members(tmp_path, n_src=300)
    t = PR.read_table(_Counting(), m_path, ["source_id", "obs_id"])
    assert pa.types.is_dictionary(t.schema.field("obs_id").type)
    assert t.equals(pq.ParquetFile(m_path).read(columns=["source_id", "obs_id"]))
    with pytest.raises(FileNotFoundError):
        PR.read_table(fsspec.filesystem("file"), str(tmp_path / "missing.parquet"), ["flags"])


def _members(tmp_path, n_src=6000, seed=1, row_group_size=100_000):
    """A members.parquet-like file: sorted source_id, each with 1–150 rows; obs_id a dictionary column, detector int8,
    dot_row int32, plus a float column with nulls and a string column too varied for a dictionary (plain pages after
    the 1 MB dictionary fills)."""
    rng = np.random.default_rng(seed)
    ids = np.sort(rng.choice(np.arange(10**9, 10**9 + 10**7), n_src, replace=False)).astype(np.int64)
    sid = np.repeat(ids, rng.integers(1, 150, n_src))
    n = sid.size
    obs = np.array([f"2025W{w:02d}_2B_{k:04d}_{e}" for w, k, e in zip(rng.integers(10, 52, n), rng.integers(0, 3000, n),
                                                                     rng.integers(1, 5, n))])
    t = pa.table({
        "source_id": sid,
        "obs_id": pa.array(obs).dictionary_encode(),
        "detector": pa.array(rng.integers(1, 7, n), pa.int8()),
        "dot_row": pa.array(rng.integers(0, 30000, n), pa.int32()),
        "x": pa.array(np.where(rng.random(n) < 0.1, None, rng.random(n)), pa.float32()),
        "note": pa.array([f"n{i:09d}-{rng.integers(0, 10**9)}" for i in range(n)]),
        "spec": pa.array([[1.0, 2.0]] * n, pa.list_(pa.float32(), 2)),
    })
    p = tmp_path / "members.parquet"
    pq.write_table(t, p, row_group_size=row_group_size, compression="zstd")
    return str(p), ids


def _full(path, cols, ids):
    df = pq.ParquetFile(path).read(columns=cols).to_pandas()
    return df[df["source_id"].isin(ids)].reset_index(drop=True)


@pytest.fixture
def pages_always(monkeypatch):
    """Walk the pages of every chunk (the small test file would otherwise be read whole)."""
    monkeypatch.setattr(PR, "MIN_HOP_BYTES", 0)
    monkeypatch.setattr(PR, "HOP_COST_BYTES", 0)
    monkeypatch.setattr(PR, "MAX_HOP_SHARE", 2.0)


COLS = ["source_id", "obs_id", "detector", "dot_row", "x", "note"]


def test_rows_by_pages_equal_the_full_read(tmp_path, pages_always):
    path, ids = _members(tmp_path)
    fs = fsspec.filesystem("file")
    md = pq.read_metadata(path)
    assert md.num_row_groups >= 3
    rng = np.random.default_rng(7)
    for want in (rng.choice(ids, 40, replace=False), ids[[10, 4000]], ids[:1], ids[-3:], np.array([5, 7], np.int64),
                 np.r_[ids[100:110], np.int64(3)], ids):
        PR.STATS.update(bytes=0, requests=0, files=0, full_bytes=0)
        got = PR.read_rows(fs, path, COLS, want)
        exp = _full(path, COLS, want)
        assert got is not None and list(got.columns) == COLS and len(got) == len(exp)
        pd.testing.assert_frame_equal(got.assign(obs_id=got["obs_id"].astype(str)),
                                      exp.assign(obs_id=exp["obs_id"].astype(str)))
        assert isinstance(got["obs_id"].dtype, pd.CategoricalDtype) and got["detector"].dtype == np.int8
        if want.size == 2:     # two sources: two row groups, one page per column in each
            assert PR.STATS["bytes"] < 0.3 * PR.STATS["full_bytes"]


def test_whole_chunks_and_order(tmp_path):
    """Default costs on a small file: every chunk read whole, same rows; columns in the asked order."""
    path, ids = _members(tmp_path, n_src=800)
    want = ids[::37]
    got = PR.read_rows(fsspec.filesystem("file"), path, ["dot_row", "source_id", "missing", "detector"], want)
    exp = _full(path, ["dot_row", "source_id", "detector"], want)
    pd.testing.assert_frame_equal(got, exp)


def test_unsupported_layouts_fall_back(tmp_path, pages_always):
    path, ids = _members(tmp_path, n_src=300)
    fs = fsspec.filesystem("file")
    assert PR.read_rows(fs, path, ["source_id", "spec"], ids[:3]) is None      # a list column
    assert PR.read_rows(fs, path, ["obs_id", "detector"], ids[:3]) is None     # no id column asked
    empty = PR.read_rows(fs, path, ["source_id", "detector"], np.zeros(0, np.int64))
    assert empty is not None and len(empty) == 0 and list(empty.columns) == ["source_id", "detector"]


def test_thrift_round_trip(tmp_path):
    """The footer parsed and written again is the same bytes (field order, types, lists, bools, binary)."""
    path, _ = _members(tmp_path, n_src=200)
    raw = open(path, "rb").read()
    n = int.from_bytes(raw[-8:-4], "little")
    footer = raw[-8 - n:-8]
    fm, end = PR.read_struct(footer)
    assert end == len(footer)
    assert bytes(PR.write_struct(fm)) == footer
    # and a page header
    col = pq.read_metadata(path).row_group(0).column(0)
    start = col.dictionary_page_offset if col.has_dictionary_page else col.data_page_offset
    h, k = PR.read_struct(raw[start:start + 256])
    assert bytes(PR.write_struct(h)) == raw[start:start + k]


def test_members_layout_like_the_bucket(tmp_path, pages_always):
    """Rows per page: Arrow cuts flat pages at 20,000 rows, the cost model's assumption."""
    path, ids = _members(tmp_path, n_src=6000, row_group_size=300_000)
    f = PR._File(fsspec.filesystem("file"), path)
    col = f.meta.row_group(0).column(3)
    start = col.dictionary_page_offset if col.has_dictionary_page else col.data_page_offset
    pages = [p for p in PR._pages(f, start, start + col.total_compressed_size) if p[2] != 2]
    assert sum(p[3] for p in pages) == f.meta.row_group(0).num_rows
    assert max(p[3] for p in pages) == PR.PAGE_ROWS


def test_read_columns_by_pages_and_its_cache(tmp_path, pages_always, monkeypatch):
    """candidates.read_columns(by_pages=True) gives the full read's frame; with --cache the rows are kept under a key
    of the ids, and an existing whole-column cache entry is used first (no read at all)."""
    from skyshift_pipeline import candidates as K
    from skyshift_pipeline.store import Store

    path, ids = _members(tmp_path, n_src=2000)
    root = tmp_path / "root"
    (root / "results" / "z").mkdir(parents=True)
    (root / "results" / "z" / "members.parquet").write_bytes(open(path, "rb").read())
    store, rel, cols = Store(str(root)), "results/z/members.parquet", ["source_id", "obs_id", "detector", "dot_row"]
    want = ids[[3, 500, 1999]]
    monkeypatch.setattr(K, "CACHE_DIR", None)
    full = K.read_columns(store, rel, cols, want)
    pd.testing.assert_frame_equal(K.read_columns(store, rel, cols, want, by_pages=True), full)
    monkeypatch.setattr(K, "SLIM_READS", False)
    pd.testing.assert_frame_equal(K.read_columns(store, rel, cols, want, by_pages=True), full)
    monkeypatch.setattr(K, "SLIM_READS", True)
    cache = tmp_path / "cache"
    monkeypatch.setattr(K, "CACHE_DIR", cache)
    PR.STATS.update(bytes=0, requests=0, files=0, full_bytes=0)
    pd.testing.assert_frame_equal(K.read_columns(store, rel, cols, want, by_pages=True), full)
    assert PR.STATS["files"] == 1 and len(list(cache.glob("*.rows-*.parquet"))) == 1
    pd.testing.assert_frame_equal(K.read_columns(store, rel, cols, want, by_pages=True), full)
    assert PR.STATS["files"] == 1                                     # the second time from the cache
    K.read_columns(store, rel, cols)                                  # whole columns cached ...
    pd.testing.assert_frame_equal(K.read_columns(store, rel, cols, ids[[7]], by_pages=True),
                                  K.read_columns(store, rel, cols, ids[[7]]))
    assert PR.STATS["files"] == 1                                     # ... and used first
