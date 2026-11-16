"""rebuild.py, the helpers of scripts/rebuild/tocheck.sh. Offline (no bucket, no gcloud). Run from pipeline/:
    cd pipeline && uv run pytest ../scripts/rebuild/tests"""

import json
import sys
from argparse import Namespace
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rebuild as R  # noqa: E402
from skyshift_pipeline import candidates as X  # noqa: E402

BUCKET = "gs://nasa-glazing-data"


def test_export_estimate_reads_flags_not_positions():
    e = R.export_estimate(100_000, int(5e9))
    assert e["positions_gb"] == 0.0
    assert e["flags_gb"] == pytest.approx(0.9 * 100_000 * R.FLAGS_READ_MB / 1e3, abs=0.1)
    assert e["total_gb"] == pytest.approx(5.0 + e["flags_gb"], abs=0.1)
    assert e["default_block_gb"] > 10 * e["total_gb"]       # the 5 MiB block reads whole dot files
    old = R.export_estimate(100_000, int(5e9), ab_files=2_000, pos_files=10_000)
    assert old["positions_gb"] == pytest.approx(10_000 * R.POSITIONS_READ_MB / 1e3, abs=0.1)


def test_overlay_reads_the_mirror_and_never_writes(tmp_path):
    rel = "results/z1/suspects.parquet"
    (tmp_path / "results" / "z1").mkdir(parents=True)
    pd.DataFrame({"source_id": [1, 2, 3], "x": [1.0, 2.0, 3.0]}).to_parquet(tmp_path / rel)
    s = R.make_overlay(str(tmp_path), BUCKET, 16384, offline=True)
    assert s.root == BUCKET                                  # the exporter's cache keys stay the bucket's
    assert s.exists(rel) and not s.exists("results/z1/members.parquet")
    assert len(s.read_parquet(rel)) == 3
    with s.fs.open(s.path(rel), "rb") as f:
        assert f.read(4) == b"PAR1"
    with pytest.raises(FileNotFoundError):
        s.fs.open(s.path("results/z1/members.parquet"), "rb")
    with pytest.raises(PermissionError):
        s.write_bytes(rel, b"x")
    with pytest.raises(PermissionError):
        s.fs.open(s.path(rel), "wb")
    with pytest.raises(PermissionError):
        s.fs.rm(s.path(rel))
    assert s.listdir("results/z1") == ["suspects.parquet"]


def test_overlay_column_reads_use_the_bucket_cache_key(tmp_path, monkeypatch):
    rel = "results/z1/sources.parquet"
    (tmp_path / "data" / "results" / "z1").mkdir(parents=True)
    pd.DataFrame({"source_id": [5, 6], "ra": [1.0, 2.0], "dec": [3.0, 4.0]}).to_parquet(tmp_path / "data" / rel)
    cache = tmp_path / "cache"
    monkeypatch.setattr(X, "CACHE_DIR", cache)
    s = R.make_overlay(str(tmp_path / "data"), BUCKET, 16384, offline=True)
    df = X.read_columns(s, rel, ["source_id", "ra"], [6])
    assert df["source_id"].tolist() == [6]
    import hashlib
    h = hashlib.sha1(f"{BUCKET}|{rel}|source_id,ra".encode()).hexdigest()[:16]
    assert (cache / f"results__z1__sources.parquet.{h}.parquet").exists()


def _cand(i, kind="mover", grade="B", status="open", zone="ecl04", **kw):
    return {"id": i, "kind": kind, "ra": 1.0, "dec": 2.0, "zone": zone, "score": 1.0, "status": status,
            "checks": [{"code": "x", "level": "ok"}], "grade": grade if kind == "mover" else None, **kw}


def _write(d: Path, main: list[dict], more: dict[str, list[dict]], zones=("ecl04",)):
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidates.json").write_text(json.dumps({"zones": list(zones), "candidates": main}))
    if more:
        (d / "candidates-more").mkdir()
        for z, cs in more.items():
            (d / "candidates-more" / f"{z}.json").write_text(json.dumps({"zone": z, "candidates": cs}))


def _report(d: Path, **kw):
    return R.cmd_report(Namespace(data_dir=str(d), checks_dir=kw.get("checks_dir"), previous=None,
                                  expect_frames=kw.get("expect_frames", False), out=str(d / "check.md")))


def test_report_passes_and_counts_the_more_files(tmp_path):
    main = [_cand("a", grade="A", frames_url="cand/a/frames.json"),
            _cand("k", grade="C", status="known", known_name="306P/LINEAR"), _cand("h", kind="high_pm")]
    _write(tmp_path, main, {"ecl04": [_cand("c1", grade="C"), _cand("c2", grade="C")]})
    assert _report(tmp_path, expect_frames=True) == 0
    text = (tmp_path / "check.md").read_text()
    assert "| mover | 4 | 3 | 1 | 0 | 1 | 0 | 3 | 2 |" in text
    assert "no hard failure" in text


def test_report_stops_without_306P_or_on_a_duplicate_id(tmp_path):
    _write(tmp_path / "a", [_cand("a")], {})
    with pytest.raises(SystemExit) as e:
        _report(tmp_path / "a")
    assert e.value.code == 2
    assert "306P/LINEAR is not listed" in (tmp_path / "a" / "check.md").read_text()
    _write(tmp_path / "b", [_cand("a", zone="ecl10")], {"ecl10": [_cand("a", grade="C", zone="ecl10")]}, zones=("ecl10",))
    with pytest.raises(SystemExit):
        _report(tmp_path / "b")
    assert "is in candidates.json and ecl10.json" in (tmp_path / "b" / "check.md").read_text()


def test_report_stops_on_side_file_ids_of_another_export(tmp_path):
    _write(tmp_path / "d", [_cand("a", zone="ecl10")], {}, zones=("ecl10",))
    chk = tmp_path / "checks"
    chk.mkdir()
    (chk / "pixel.json").write_text(json.dumps({"source": "pixel", "candidates": {"zzz": {"checks": [], "status": "open"}}}))
    with pytest.raises(SystemExit):
        _report(tmp_path / "d", checks_dir=str(chk))


def test_combine_puts_main_and_more_in_one_file(tmp_path):
    _write(tmp_path, [_cand("a")], {"ecl04": [_cand("c1", grade="C")]})
    out = tmp_path / "all.json"
    R.cmd_combine(Namespace(main=str(tmp_path / "candidates.json"), more=str(tmp_path / "candidates-more"), out=str(out)))
    assert [c["id"] for c in json.loads(out.read_text())["candidates"]] == ["a", "c1"]


def test_fill_todo_lists_hours_frames_without_fill(tmp_path, capsys):
    for i, clock, fill in (("x", "hours", False), ("y", "hours", True), ("z", "months", False), ("w", "hours", False)):
        p = tmp_path / "cand" / i
        p.mkdir(parents=True)
        (p / "frames.json").write_text(json.dumps({"clock": clock}))
        if fill:
            (p / "fill.json").write_text("{}")
    (tmp_path / "done.txt").write_text("w\n")
    R.cmd_fill_todo(Namespace(data_dir=str(tmp_path), done=str(tmp_path / "done.txt")))
    assert capsys.readouterr().out.split() == ["x"]
