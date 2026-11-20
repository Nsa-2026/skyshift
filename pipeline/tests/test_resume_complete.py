"""Resume after a crash (20 Nov): the 18 Nov bench was cut off by a machine crash and left 0-byte dot, peaks, streak
and forced files; a resumed run skipped every dot file that existed, so those photos stayed empty. Now only a whole
parquet file counts as done (Store.complete_parquet / complete_parquets), and local writes are fsynced before the
rename."""

import io
import os

import pandas as pd

from skyshift_pipeline import dots as D
from skyshift_pipeline.store import Store, dots_rel

from conftest import CUTOUT


def _parquet_bytes() -> bytes:
    buf = io.BytesIO()
    pd.DataFrame({"a": [1, 2, 3]}).to_parquet(buf, index=False)
    return buf.getvalue()


def test_complete_parquet_tells_whole_files_from_crash_leftovers(tmp_path):
    store = Store(str(tmp_path))
    data = _parquet_bytes()
    store.write_bytes("d/whole.parquet", data)
    (tmp_path / "d" / "empty.parquet").write_bytes(b"")
    (tmp_path / "d" / "cut.parquet").write_bytes(data[: len(data) // 2])  # cut short: no trailing magic
    (tmp_path / "d" / "tiny.parquet").write_bytes(b"PAR1PAR1")
    (tmp_path / "d" / "note.json").write_bytes(b"{}")
    assert store.complete_parquet("d/whole.parquet")
    for name in ("empty", "cut", "tiny", "missing"):
        assert not store.complete_parquet(f"d/{name}.parquet"), name
    assert store.complete_parquets("d") == {"whole.parquet"}
    assert store.complete_parquets("nothing-here") == set()
    assert not [n for n in os.listdir(tmp_path / "d") if ".tmp-" in n]  # the temporary name is gone after the rename


def test_already_done_redoes_a_zero_byte_dot_file(tmp_path):
    store = Store(str(tmp_path))
    rel = dots_rel("X", 1)
    store.write_bytes(rel, b"")
    st = D._new_stats("X", 1)
    assert not D._already_done(store, rel, st) and st.get("incomplete_replaced") and st["status"] == ""
    store.write_bytes(rel, _parquet_bytes())
    st = D._new_stats("X", 1)
    assert D._already_done(store, rel, st) and st["status"] == "skipped" and "incomplete_replaced" not in st
    st = D._new_stats("Y", 1)
    assert not D._already_done(store, dots_rel("Y", 1), st) and "incomplete_replaced" not in st


def test_process_photo_measures_again_over_a_zero_byte_dot_file(tmp_path):
    rel = dots_rel("2025W18_1B_0074_2", 1)
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")  # what the crash left
    st = D.process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2)
    assert st["status"] == "done" and st.get("incomplete_replaced"), st
    assert len(pd.read_parquet(p)) == st["n_dots"] > 50
    assert D.process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT))["status"] == "skipped"
