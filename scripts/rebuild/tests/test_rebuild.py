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
    return {"id": i, "kind": kind, "ra": 1.0, "dec": 2.0, "zone": zone, "score": 1.0, "status": status, "det": 2,
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
                                  expect_frames=kw.get("expect_frames", False), frames_top=kw.get("frames_top", 300),
                                  out=str(d / "check.md")))


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


def test_frames_top_is_the_best_open_a_b_movers_and_the_report_counts_only_them(tmp_path, capsys):
    main = [_cand("a1", grade="A", score=0.9, frames_url="cand/a1/frames.json"), _cand("b1", score=0.8),
            _cand("b2", score=0.7), _cand("k", grade="A", score=0.99, status="known", known_name="306P/LINEAR"),
            _cand("f", score=0.95, status="flaw"), _cand("c", grade="C", score=0.98), _cand("n", score=0.97, det=None)]
    assert [c["id"] for c in R.frames_top(main, 2)] == ["a1", "b1"]
    _write(tmp_path, main, {})
    R.cmd_frames_check(Namespace(data_dir=str(tmp_path), top=2))
    assert capsys.readouterr().out.split() == ["1", "2"]
    assert _report(tmp_path, expect_frames=True, frames_top=1) == 0      # a1 has frames; the others open live
    assert "1 of the top 1 open grade A/B movers" in (tmp_path / "check.md").read_text()
    assert _report(tmp_path, expect_frames=True, frames_top=300) == 0    # 2 missing: a warning (at most 5 or 5%)
    assert "2 of the top 3 open A/B movers without static frames" in (tmp_path / "check.md").read_text()


# ---- step a0: refcat completion (no network: refcat.fetch_tile replaced)

def test_refcat_need_follows_the_config_limit():
    assert R.refcat_need(["gaia"], 19.99, 20.0) == (False, True)            # Gaia-only: add CatWISE + 2MASS
    assert R.refcat_need(None, 19.99, 20.0) == (False, False)               # no marker: built with every catalogue
    assert R.refcat_need(["gaia", "catwise", "2mass"], 19.99, 21.0) == (True, False)   # G max raised: Gaia again
    assert R.refcat_need(["gaia", "catwise", "2mass"], 20.6, 21.0, a0_g_max=21.0) == (False, False)


def _rows(cat, n, ra0, dec0, g=None):
    import numpy as np

    from skyshift_pipeline import refcat
    rng = np.random.default_rng(len(cat) + n)
    out = {"ref_id": [f"{cat}:{ra0:.2f}:{dec0:.2f}:{i}" for i in range(n)], "cat": [cat] * n,
           "ra": ra0 + rng.uniform(0, 0.99, n), "dec": dec0 + rng.uniform(0, 0.99, n), "epoch_mjd": np.full(n, 57388.5)}
    if g is not None:
        out["g_mag"] = np.linspace(12.0, g, n)
    if cat == "catwise":
        out["w1_mag"], out["w2_mag"] = np.full(n, 15.0), np.full(n, 14.0)
    return refcat._finish(out, n)


def _fake_fetch(g_max, calls):
    def fetch(cat, tile, tap=None, s3=None):
        calls.append((cat, tile.key))
        return _rows(cat, 5, tile.ra0, tile.dec0, g=g_max - 0.05 if cat == "gaia" else None), f"fake {cat}"
    return fetch


def test_refcat_completes_a_gaia_only_refcat_and_step_a_keeps_it(tmp_path, monkeypatch):
    from skyshift_pipeline import config as C
    from skyshift_pipeline import refcat
    monkeypatch.setattr(C, "REFCAT_S3_FIRST", C.REFCAT_S3_FIRST)          # cmd_refcat sets both: put them back after
    monkeypatch.setenv("SKYSHIFT_REFCAT_S3_FIRST", "0")
    zone = C.ZONES["pilot"]
    ts = refcat.tiles(*refcat.query_box(zone))
    gaia = pd.concat([_rows("gaia", 4, t.ra0, t.dec0, g=19.98) for t in ts], ignore_index=True)
    (tmp_path / "refcat").mkdir()
    gaia.to_parquet(tmp_path / "refcat" / "pilot.parquet")
    (tmp_path / "refcat" / "pilot_catalogs.json").write_text(json.dumps({"catalogs": ["gaia"], "gspc": True}))
    bucket_size = (tmp_path / "refcat" / "pilot.parquet").stat().st_size
    assert R.refcat_state(tmp_path, "pilot")["state"] == "todo"
    calls = []
    monkeypatch.setattr(refcat, "fetch_tile", _fake_fetch(20.0, calls))
    monkeypatch.setattr(C, "REFCAT_GAIA_G_MAX", 20.0)
    R.cmd_refcat(Namespace(data=str(tmp_path), zone="pilot", workers=2))
    assert {c for c, _ in calls} == {"catwise", "2mass"} and len(calls) == 2 * len(ts)   # Gaia kept, not asked again
    st = R.refcat_state(tmp_path, "pilot")
    assert st["state"] == "ok" and st["rows"]["gaia"] == len(gaia) and st["rows"]["catwise"] == 5 * len(ts)
    marker = json.loads((tmp_path / "refcat" / "pilot_catalogs.json").read_text())
    assert marker["catalogs"] == ["gaia", "catwise", "2mass"] and marker["rebuild_a0"]["gaia"] == "kept"
    assert (tmp_path / "refcat" / "pilot_forced.parquet").exists()
    assert (tmp_path / "refcat" / "pilot.bucket.parquet").stat().st_size == bucket_size
    assert R._ok(tmp_path / "refcat" / "pilot.parquet", bucket_size)            # step a won't copy the bucket's over it
    assert not R._ok(tmp_path / "refcat" / "pilot_forced.parquet", 1)
    # nothing to do the second time
    calls.clear()
    R.cmd_refcat(Namespace(data=str(tmp_path), zone="pilot", workers=2))
    assert calls == []
    # REFCAT_GAIA_G_MAX raised to 21: Gaia again (the old tiles dropped), CatWISE and 2MASS kept
    monkeypatch.setattr(C, "REFCAT_GAIA_G_MAX", 21.0)
    monkeypatch.setattr(refcat, "fetch_tile", _fake_fetch(21.0, calls))
    assert R.refcat_state(tmp_path, "pilot")["gaia"] is True
    R.cmd_refcat(Namespace(data=str(tmp_path), zone="pilot", workers=2))
    assert {c for c, _ in calls} == {"gaia"} and len(calls) == len(ts)
    st = R.refcat_state(tmp_path, "pilot")
    assert st["state"] == "ok" and st["g_faintest"] > 20.9 and st["rows"]["catwise"] == 5 * len(ts)
    marker = json.loads((tmp_path / "refcat" / "pilot_catalogs.json").read_text())
    assert marker["rebuild_a0"]["gaia_g_max"] == 21.0 and marker["rebuild_a0"]["catwise_2mass"] == "kept"


# ---- step m: the PR #388 chain on the mirror (offline)

def _listing(work: Path, pattern: str, rows: list[tuple[str, int]]):
    import hashlib
    d = work / "plan" / "listing"
    d.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{s:>12}  2026-11-16T00:00:00Z  {u}\n" for u, s in rows)
    (d / f"ls-{hashlib.sha1(pattern.encode()).hexdigest()[:12]}.txt").write_text(text)


def test_mplan_lists_tables_and_each_dot_file_once_and_mclean_keeps_shared_ones(tmp_path, capsys):
    data, work = tmp_path / "data", tmp_path / "work"
    for z, photos in (("ecl04", ["A", "B"]), ("ecl05", ["B", "C"])):
        (data / "photolists" / "zones").mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"obs_id": photos, "detector": [1] * len(photos)}).to_parquet(data / "photolists" / "zones" / f"{z}.parquet")
        (data / "results" / z).mkdir(parents=True)
        (data / "results" / z / "summary.json").write_text(json.dumps({"steps": {"link": {"dots_dir": "dots"}}}))
    _listing(work, f"{BUCKET}/results/**", [(f"{BUCKET}/results/{z}/{n}", 100) for z in ("ecl04", "ecl05")
                                            for n in ("sources.parquet", "members.parquet", "calib.json", "movers.parquet")])
    _listing(work, f"{BUCKET}/dots/*", [(f"{BUCKET}/dots/{o}D1.parquet", 1000) for o in "ABCD"])
    R.cmd_mplan(Namespace(data=str(data), work=str(work), zones="ecl05,ecl04", zones_file=None))
    plan = json.loads((work / "plan" / "motion_plan.json").read_text())
    assert plan["zones"] == ["ecl04", "ecl05"]                         # sky order (RA)
    assert plan["dots_bytes_unique"] == 3000 and plan["tables_bytes"] == 600   # B shared, copied once; movers not read
    assert plan["per_zone"]["ecl04"]["dots_files"] == 2 and plan["per_zone"]["ecl04"]["exact"]
    assert plan["dots_peak_bytes"] == 2000                              # A goes after ecl04, B stays for ecl05
    rows = R.read_manifest(work, "motion_manifest.tsv")
    assert sorted(r["local"] for r in rows if r["zone"] == "ecl04" and r["category"] == "mdots") == ["dots/AD1.parquet", "dots/BD1.parquet"]
    for o in "ABC":
        (data / "dots").mkdir(exist_ok=True)
        (data / "dots" / f"{o}D1.parquet").write_bytes(b"x" * 1000)
    stamps = tmp_path / "zones"
    stamps.mkdir()
    (stamps / "m.ecl04").write_text("done")
    R.cmd_mclean(Namespace(data=str(data), work=str(work), stamps=str(stamps)))
    assert sorted(p.name for p in (data / "dots").iterdir()) == ["BD1.parquet", "CD1.parquet"]   # B: ecl05 still needs it
    (stamps / "m.ecl05").write_text("done")
    R.cmd_mclean(Namespace(data=str(data), work=str(work), stamps=str(stamps)))
    assert list((data / "dots").iterdir()) == []


def test_motion_runs_the_pipeline_step_on_the_mirror_and_writes_only_there(tmp_path):
    import numpy as np

    from skyshift_pipeline import refcat
    data = tmp_path / "data"
    (data / "results" / "pilot").mkdir(parents=True)
    (data / "photolists" / "zones").mkdir(parents=True)
    (data / "refcat").mkdir()
    ra, dec = np.array([121.0, 122.0, 123.0]), np.array([15.0, 16.0, 17.0])
    pd.DataFrame({"source_id": [1, 2, 3], "ra": ra, "dec": dec}).to_parquet(data / "results" / "pilot" / "sources.parquet")
    pd.DataFrame({"source_id": [1, 2, 3], "obs_id": ["o1"] * 3, "detector": [1] * 3, "dot_row": [0, 1, 2]}).to_parquet(
        data / "results" / "pilot" / "members.parquet")
    pd.DataFrame({"obs_id": ["o1"], "detector": [1], "mjd": [61000.0]}).to_parquet(data / "photolists" / "zones" / "pilot.parquet")
    n = 3
    refcat._finish({"ref_id": [f"gaia:{i}" for i in range(n)], "cat": ["gaia"] * n, "ra": ra + 1e-5, "dec": dec,
                    "epoch_mjd": np.full(n, 57388.5), "g_mag": np.full(n, 15.0)}, n).to_parquet(data / "refcat" / "pilot.parquet")
    rc = R.cmd_motion(Namespace(data=str(data), zone="pilot", step="crossid", bucket=BUCKET, workers=2, block=16384,
                                offline=True))
    assert rc == 0
    x = pd.read_parquet(data / "results" / "pilot" / "crossids.parquet")
    assert len(x) == 3 and (x["gaia_ref_id"] != "").all()
    assert "crossid" in json.loads((data / "results" / "pilot" / "summary.json").read_text())["steps"]
