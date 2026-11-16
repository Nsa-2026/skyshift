"""Parquet reads by exact range requests: some columns (read_table), or only the pages holding some rows (read_rows).

read_table: a file opened through gcsfs fetches 5 MiB blocks (its default read-ahead), so reading one column of a
2.5 MB dot file pulled most of the file (100 local dot files: median 1.67 MB for the flags column). Here every read is
one exact range: the file's last 8 bytes (with its size, one request on GCS), the footer, then the column chunks as
pyarrow asks for them. A dot file's footer is ~63 KB, nearly all key-value metadata (ARROW:schema 35 KB, the photo's
facts 21 KB, pandas 3 KB) that comes after the schema and row groups (3.4 KB) in the thrift: when every asked column is
a plain number (no dictionary, string, list or time type), only the footer's first 4 KB are read and pyarrow takes the
types from the parquet schema, which for those columns are the ones written. A flags read is then ~9 KB in 3 requests.

read_rows: pyarrow reads whole column chunks, and our zone tables are written in big row groups (members.parquet:
1,048,576 rows, sources.parquet: one group), so row-group filters skip nothing: the candidates' ids sit in every group. But Arrow
(≥ 15) cuts every data page at 20,000 rows, so a flat column chunk is ~53 pages of 13–40 KB. read_rows reads the id
column, finds the rows whose id is wanted, walks each other column's page headers (one small range read per page)
and fetches only the pages that hold those rows. pyarrow decodes them: the pages are copied as they are into a small
single-column parquet file with its own footer (the original column's schema, codec and Arrow type).

Flat columns only (no lists or structs): a flat page's num_values is its row count. Anything unexpected (a nested
column, pages that don't add up to the group's rows, a thrift field we can't parse) returns None and the caller reads
the whole columns as before. The result equals pyarrow's full read filtered to the ids, rows in file order.
"""

from __future__ import annotations

import base64
import io
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PREFIX_BYTES = 4096       # first read of a footer, from its start (a dot file needs 3.4 KB: schema, row groups)
HEADER_BYTES = 256      # one page header read (re-read larger when a header is longer)
HOP_COST_BYTES = 8192     # a range request costs about as much as 8 KB on Nearline ($0.001 / 1,000 vs $0.13 / GB)
MIN_HOP_BYTES = 262144    # a column chunk smaller than this is read whole
MAX_HOP_SHARE = 0.7       # walk the pages only when that is expected to cost < 70% of the whole chunk
PAGE_ROWS = 20000         # Arrow's default data_page_row_count_limit (≥ 15), for the cost estimate only
DICT_ENCODINGS = (2, 8)   # PLAIN_DICTIONARY, RLE_DICTIONARY: the page needs the chunk's dictionary page

STATS = {"tables": 0, "files": 0, "bytes": 0, "requests": 0, "full_bytes": 0}  # read_table calls, read_rows files (full_bytes: their whole columns), every range read
_lock = threading.Lock()


class Unsupported(Exception):
    """The file's layout isn't one read_rows handles: read the whole columns instead."""


# --- thrift compact protocol: enough of it for parquet's footer and page headers --------------------------------
# A struct is a list of [field id, compact type, value]; a list is ("L", element type, [values]); binary is bytes.
def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, i


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _read_value(b: bytes, i: int, t: int):
    if t == 3:
        return b[i], i + 1
    if t in (4, 5, 6):
        v, i = _varint(b, i)
        return _zz(v), i
    if t == 7:
        return bytes(b[i:i + 8]), i + 8
    if t == 8:
        n, i = _varint(b, i)
        return bytes(b[i:i + n]), i + n
    if t in (9, 10):
        h = b[i]
        i += 1
        n, et = h >> 4, h & 0x0F
        if n == 15:
            n, i = _varint(b, i)
        out = []
        for _ in range(n):
            if et in (1, 2):
                v, i = b[i], i + 1          # a bool element: one byte, kept as it is
            else:
                v, i = _read_value(b, i, et)
            out.append(v)
        return ("L", et, out), i
    if t == 12:
        return read_struct(b, i)
    raise Unsupported(f"thrift type {t}")


def read_struct(b: bytes, i: int = 0) -> tuple[list, int]:
    out, fid = [], 0
    while True:
        h = b[i]
        i += 1
        if h == 0:
            return out, i
        d, t = h >> 4, h & 0x0F
        if d:
            fid += d
        else:
            v, i = _varint(b, i)
            fid = _zz(v)
        if t in (1, 2):
            out.append([fid, t, t == 1])
        else:
            v, i = _read_value(b, i, t)
            out.append([fid, t, v])


def _w_varint(n: int, out: bytearray) -> None:
    while True:
        if n < 0x80:
            out.append(n)
            return
        out.append((n & 0x7F) | 0x80)
        n >>= 7


def _w_value(t: int, v, out: bytearray) -> None:
    if t == 3:
        out.append(v & 0xFF)
    elif t in (4, 5, 6):
        _w_varint((v << 1) ^ (v >> 63), out)
    elif t == 7:
        out += v
    elif t == 8:
        _w_varint(len(v), out)
        out += v
    elif t in (9, 10):
        _, et, vals = v
        if len(vals) < 15:
            out.append((len(vals) << 4) | et)
        else:
            out.append(0xF0 | et)
            _w_varint(len(vals), out)
        for x in vals:
            if et in (1, 2):
                out.append(x)
            else:
                _w_value(et, x, out)
    elif t == 12:
        write_struct(v, out)
    else:
        raise Unsupported(f"thrift type {t}")


def write_struct(fields: list, out: bytearray | None = None) -> bytearray:
    out = bytearray() if out is None else out
    last = 0
    for fid, t, v in sorted(fields, key=lambda f: f[0]):
        ct = (1 if v else 2) if t in (1, 2) else t
        d = fid - last
        if 0 < d <= 15:
            out.append((d << 4) | ct)
        else:
            out.append(ct)
            _w_varint((fid << 1) ^ (fid >> 63), out)
        if t not in (1, 2):
            _w_value(t, v, out)
        last = fid
    out.append(0)
    return out


def _get(s: list, fid: int, default=None):
    for f in s:
        if f[0] == fid:
            return f[2]
    return default


def _with(s: list, keep: tuple[int, ...], **set_: tuple[int, int, object]) -> list:
    """A copy of struct s with only the fields in keep, then the given (fid, type, value) set."""
    out = [list(f) for f in s if f[0] in keep]
    for fid, t, v in set_.values():
        out = [f for f in out if f[0] != fid] + [[fid, t, v]]
    return out


# --- exact range reads ------------------------------------------------------------------------------------------
def _count(nbytes: int, requests: int = 1) -> None:
    with _lock:
        STATS["bytes"] += nbytes
        STATS["requests"] += requests


def _cat(fs, path: str, start: int, end: int) -> bytes:
    b = fs.cat_file(path, start=start, end=end)
    _count(len(b))
    return b


def _size_and_end(fs, path: str) -> tuple[int, bytes]:
    """(file size, its last 8 bytes): one request on GCS (a suffix range; the size from Content-Range)."""
    call, url, loop = getattr(fs, "_call", None), getattr(fs, "url", None), getattr(fs, "loop", None)
    if call is not None and url is not None and loop is not None:
        from fsspec.asyn import sync

        try:
            headers, out = sync(loop, call, "GET", url(path), headers={"Range": "bytes=-8"})
            cr = {k.lower(): v for k, v in dict(headers).items()}.get("content-range", "")
            size = int(cr.rsplit("/", 1)[1])
            _count(len(out))
            return size, bytes(out)
        except (ValueError, IndexError, KeyError, TypeError):
            pass
    size = fs.size(path)
    _count(0)
    return size, _cat(fs, path, size - 8, size)


# Plain numbers: physical BOOLEAN, INT32, INT64, FLOAT, DOUBLE with no annotation but an integer width; the Arrow
# type pyarrow derives from the parquet schema alone is then the one written.
_NUMERIC = {0, 1, 2, 4, 5}
_INT_CONVERTED = set(range(15, 23))   # UINT_8 .. INT_64


def _plain_number(e: list) -> bool:
    lt = _get(e, 10)
    return (_get(e, 1) in _NUMERIC and not _get(e, 5) and _get(e, 6) in (None, *_INT_CONVERTED)
            and (lt is None or [f[0] for f in lt] == [10]))


class Footer:
    """A parquet file's footer by exact ranges: size, the thrift (fields; `full` when the key-value metadata and
    created_by were read too), pyarrow's FileMetaData and Arrow schema made from it."""

    def __init__(self, fs, path: str, columns: list[str] | None = None):
        self.fs, self.path = fs, path
        self.size, end = _size_and_end(fs, path)
        if end[-4:] != b"PAR1":
            raise Unsupported("not a parquet file")
        n = int.from_bytes(end[:4], "little")
        start = self.size - 8 - n
        raw, first = b"", None
        k = min(n, PREFIX_BYTES)
        while True:                                      # the schema and row groups, at the footer's start
            raw += _cat(fs, path, start + len(raw), start + k)
            try:
                first, _ = _read_fields(raw, below=5)
                break
            except IndexError:
                if k >= n:
                    raise Unsupported("footer too short") from None
                k = min(n, 4 * k)
        elems = _get(first, 2)[2]
        top = _top_spans(elems)
        plain = columns is not None and all(c not in top or (top[c][1] - top[c][0] == 1 and _plain_number(elems[top[c][0]]))
                                            for c in columns)
        if plain and len(raw) < n:
            fm = first                                   # skip the key-value metadata: numbers need no Arrow schema
            self.full = False
        else:
            if len(raw) < n:
                raw += _cat(fs, path, start + len(raw), start + n)
            fm, _ = read_struct(raw)
            self.full = True
        if _get(fm, 8) is not None:
            raise Unsupported("encrypted footer")
        self.fm = fm
        foot = raw if self.full else bytes(write_struct(fm))
        fake = io.BytesIO(b"PAR1" + foot + len(foot).to_bytes(4, "little") + b"PAR1")
        self.meta = pq.read_metadata(fake)
        self.schema = pq.read_schema(fake)


def _read_fields(b: bytes, below: int) -> tuple[list, int]:
    """The fields of the struct at b[0:] with ids < below (they come first), without reading further."""
    out, fid, i = [], 0, 0
    while True:
        h = b[i]
        if h == 0:
            return out, i
        d, t = h >> 4, h & 0x0F
        if d and fid + d >= below:
            return out, i
        i += 1
        if d:
            fid += d
        else:
            v, i = _varint(b, i)
            fid = _zz(v)
            if fid >= below:
                return out, i
        if t in (1, 2):
            out.append([fid, t, t == 1])
        else:
            v, i = _read_value(b, i, t)
            out.append([fid, t, v])


class RangeFile:
    """A read-only file object for pyarrow: every read is one exact range request (no read-ahead)."""

    closed = False

    def __init__(self, fs, path: str, size: int):
        self.fs, self.path, self._size, self.pos = fs, path, size, 0

    def seek(self, pos: int, whence: int = 0) -> int:
        self.pos = pos if whence == 0 else self.pos + pos if whence == 1 else self._size + pos
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int = -1) -> bytes:
        n = self._size - self.pos if n is None or n < 0 else min(n, self._size - self.pos)
        b = _cat(self.fs, self.path, self.pos, self.pos + n) if n > 0 else b""
        self.pos += len(b)
        return b

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def read_table(fs, path: str, columns: list[str]) -> pa.Table:
    """Columns `columns` (those the file has, in this order) of a parquet file, by exact range requests: the footer,
    then one request per column chunk. What pq.ParquetFile(fs.open(path)).read(columns) gives, with fewer bytes."""
    f = Footer(fs, path, columns)
    pf = pq.ParquetFile(RangeFile(fs, path, f.size), metadata=f.meta)
    have = set(pf.schema_arrow.names)
    t = pf.read(columns=[c for c in columns if c in have])
    with _lock:
        STATS["tables"] += 1
    return t


class _File:
    def __init__(self, fs, path: str):
        self.fs, self.path = fs, path
        f = Footer(fs, path)
        self.fm, self.meta, self.schema = f.fm, f.meta, f.schema

    def cat(self, start: int, end: int) -> bytes:
        return _cat(self.fs, self.path, start, end)


def _top_spans(elems: list) -> dict[str, tuple[int, int]]:
    """Top-level field name -> (first, end) indices of its schema elements (the root is element 0)."""
    spans, i = {}, 1

    def span(j: int) -> int:
        n = _get(elems[j], 5, 0) or 0
        k = j + 1
        for _ in range(n):
            k = span(k)
        return k

    for _ in range(_get(elems[0], 5, 0) or 0):
        e = span(i)
        spans[_get(elems[i], 4).decode()] = (i, e)
        i = e
    return spans


def _one_column(f: _File, rg: int, leaf: int, name: str, chunks: list[bytes], first: int, num_values: int,
                num_rows: int, has_dict: bool) -> pa.Array:
    """Decode some pages of one column chunk: they are written, as they are, into a one-column parquet file."""
    elems = _get(f.fm, 2)[2]
    a, e = _top_spans(elems)[name]
    root = _with(elems[0], (1, 2, 3, 4, 6, 7, 8, 9, 10), n=(5, 5, 1))
    cc = _get(f.fm, 4)[2][rg]
    cc = _get(cc, 1)[2][leaf]
    cmd = _get(cc, 3)
    body = b"".join(chunks)
    dpo = 4 + (first if has_dict else 0)
    md = _with(cmd, (1, 2, 3, 4), nv=(5, 6, num_values), us=(6, 6, len(body)), cs=(7, 6, len(body)),
               dp=(9, 6, dpo))
    if has_dict:
        md = _with(md, (1, 2, 3, 4, 5, 6, 7, 9), dict=(11, 6, 4))
    chunk = [[2, 6, 4], [3, 12, md]]
    group = [[1, 9, ("L", 12, [chunk])], [2, 6, len(body)], [3, 6, num_rows]]
    field = f.schema.field(name)
    kv = [[1, 8, b"ARROW:schema"], [2, 8, base64.b64encode(pa.schema([field]).serialize().to_pybytes())]]
    fm = [[1, 5, _get(f.fm, 1, 1)], [2, 9, ("L", 12, [root] + elems[a:e])], [3, 6, num_rows],
          [4, 9, ("L", 12, [group])], [5, 9, ("L", 12, [kv])]]
    if _get(f.fm, 6) is not None:
        fm.append([6, 8, _get(f.fm, 6)])
    foot = bytes(write_struct(fm))
    data = b"PAR1" + body + foot + len(foot).to_bytes(4, "little") + b"PAR1"
    t = pq.read_table(io.BytesIO(data))
    if t.num_rows != num_rows or t.schema.field(0).type != field.type:
        raise Unsupported(f"{name}: decoded {t.num_rows} rows of {t.schema.field(0).type}")
    return t.column(0).combine_chunks()


def _pages(f: _File, start: int, end: int) -> list[tuple[int, int, int, int, int]]:
    """(offset, total bytes, page type, num_values, encoding) of every page between start and end, from the headers."""
    out, pos = [], start
    while pos < end:
        n = HEADER_BYTES
        while True:
            b = f.cat(pos, min(pos + n, end))
            try:
                h, k = read_struct(b)
                break
            except IndexError:
                if pos + n >= end:
                    raise Unsupported("truncated page header") from None
                n *= 8
        typ, size = _get(h, 1), _get(h, 3)
        if typ == 0:
            nv, enc = _get(_get(h, 5), 1), _get(_get(h, 5), 2)
        elif typ == 3:
            nv, enc = _get(_get(h, 8), 3), _get(_get(h, 8), 4)   # v2: num_rows
        elif typ == 2:
            nv, enc = 0, None
        else:
            raise Unsupported(f"page type {typ}")
        out.append((pos, k + size, typ, nv, enc))
        pos += k + size
    if pos != end:
        raise Unsupported("pages overrun the chunk")
    return out


def _chunk(f: _File, rg: int, leaf: int, name: str, rows: np.ndarray | None) -> np.ndarray | pa.Array:
    """Column `name` of row group rg at the local rows `rows` (all rows when None), read by pages when it pays."""
    col = f.meta.row_group(rg).column(leaf)
    n_rows = f.meta.row_group(rg).num_rows
    start = col.dictionary_page_offset if col.has_dictionary_page else col.data_page_offset
    size = col.total_compressed_size
    whole = rows is None or size < MIN_HOP_BYTES
    if not whole:
        per = max(1, min(PAGE_ROWS, int(n_rows * 1048576 / max(col.total_uncompressed_size, 1))))
        est_pages = math.ceil(n_rows / per)
        est = est_pages * HOP_COST_BYTES + np.unique(rows // per).size / est_pages * size
        whole = est >= MAX_HOP_SHARE * size
    if whole:
        arr = _one_column(f, rg, leaf, name, [f.cat(start, start + size)], col.data_page_offset - start,
                          col.num_values, n_rows, col.has_dictionary_page)
        return arr if rows is None else arr.take(pa.array(rows))
    pages = _pages(f, start, start + size)
    data = [p for p in pages if p[2] != 2]
    nv = np.array([p[3] for p in data], np.int64)
    if nv.sum() != n_rows:
        raise Unsupported(f"{name}: pages hold {nv.sum()} values for {n_rows} rows")
    first_row = np.r_[0, np.cumsum(nv)[:-1]]
    pick = np.unique(np.searchsorted(first_row, rows, side="right") - 1)
    sel = [data[i] for i in pick]
    has_dict = pages[0][2] == 2 and any(p[4] in DICT_ENCODINGS for p in sel)
    ranges = ([(pages[0][0], pages[0][0] + pages[0][1])] if has_dict else [])
    for p in sel:                                       # adjacent pages: one request
        if ranges and ranges[-1][1] == p[0]:
            ranges[-1] = (ranges[-1][0], p[0] + p[1])
        else:
            ranges.append((p[0], p[0] + p[1]))
    parts = [f.cat(a, b) for a, b in ranges]
    arr = _one_column(f, rg, leaf, name, parts, pages[0][1] if has_dict else 0, int(nv[pick].sum()),
                      int(nv[pick].sum()), has_dict)
    # local row -> position in the picked pages
    base = np.cumsum(np.r_[0, nv[pick][:-1]])
    k = np.searchsorted(first_row[pick], rows, side="right") - 1
    return arr.take(pa.array(base[k] + rows - first_row[pick][k]))


def read_rows(fs, path: str, columns: list[str], ids, id_col: str = "source_id", workers: int = 16
              ) -> pd.DataFrame | None:
    """The rows of `path` whose id_col is in ids, columns `columns` (those the file has, in this order), as
    pyarrow's full read filtered to them would give (rows in file order); None when the file can't be read this way
    (then read the whole columns). Range requests and bytes go to STATS."""
    try:
        f = _File(fs, path)
        have = set(f.schema.names)
        cols = [c for c in columns if c in have]
        if id_col not in cols:
            return None
        leaves = {}
        for j in range(f.meta.num_columns):
            p = f.meta.schema.column(j).path
            if p.split(".")[0] in cols:
                if p != p.split(".")[0] or p in leaves:
                    return None                                  # nested: not handled here
                leaves[p] = j
        want = np.unique(np.asarray(ids, np.int64))
        full = sum(f.meta.row_group(g).column(j).total_compressed_size
                   for g in range(f.meta.num_row_groups) for j in leaves.values())
        groups = []
        for g in range(f.meta.num_row_groups):
            st = f.meta.row_group(g).column(leaves[id_col]).statistics
            if st is not None and st.has_min_max and want.size:
                lo, hi = np.searchsorted(want, [st.min, st.max], side="left")[0], np.searchsorted(want, st.max, "right")
                if hi <= lo:
                    continue
            elif not want.size:
                continue
            groups.append(g)

        def one(g):
            idv = _chunk(f, g, leaves[id_col], id_col, None).to_numpy(zero_copy_only=False)
            rows = np.flatnonzero(np.isin(idv, want))
            if not rows.size:
                return g, None
            out = {id_col: pa.array(idv[rows], f.schema.field(id_col).type)}
            for c in cols:
                if c != id_col:
                    out[c] = _chunk(f, g, leaves[c], c, rows)
            return g, out

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            got = [o for _, o in sorted(ex.map(one, groups), key=lambda x: x[0]) if o is not None]
        schema = pa.schema([f.schema.field(c) for c in cols])
        with _lock:
            STATS["files"] += 1
            STATS["full_bytes"] += full
        if not got:
            return schema.empty_table().to_pandas()
        t = pa.Table.from_arrays([pa.chunked_array([o[c] for o in got], schema.field(c).type) for c in cols],
                                 schema=schema)
        return t.to_pandas()
    except (Unsupported, IndexError, KeyError, ValueError, pa.ArrowException):
        return None
