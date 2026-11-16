"""Session stacks fused into the extraction-v2 dots worker (dots.process_photos with `stacks`, coadd.Stacker;
docs/session-stacks.md §8): the same files as dots v2 followed by the separate `sessionstack` step, each photo read once.
The real cutout (fixtures/fits) as a whole photo: three exposures of one session, D1 and D4 (the same pixels loaded as
both detectors, as test_extraction_v2 does), on the same pointing."""

from __future__ import annotations

import shutil

import pandas as pd
import pytest
from skyshift_core import open_level2

import skyshift_pipeline.config as C
from skyshift_pipeline import coadd as CO
from skyshift_pipeline import dots as D

from test_dots_extras import synthetic_mef

V2 = C.DOTS_V2_SETTINGS
STEP = 2.1 / 1440.0
OBS = ["S1_0001_1", "S1_0001_2", "S1_0001_3"]
TIMING = {"wall_s", "cpu_s", "prepare", "fused_in_dots"}


@pytest.fixture
def session(tmp_path, monkeypatch):
    path = synthetic_mef(tmp_path)
    reads: list[str] = []

    def load(url):
        obs, det = url.rsplit("|", 1)
        reads.append(url)
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        l2.detector = int(det)
        l2.mjd_avg = float(l2.mjd_avg) + STEP * OBS.index(obs)
        return l2

    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(C, "SESSION_STACK", True)
    monkeypatch.setattr(D, "load_photo", load)
    rows = [{"obs_id": o, "detector": d, "url": f"{o}|{d}", "session": 5, "visit": 1, "mjd": 61000.0 + STEP * k}
            for k, o in enumerate(OBS) for d in (1, 4)]
    pl = pd.DataFrame(rows)
    photos = [(r.obs_id, int(r.detector), r.url, int(r.session), int(r.visit)) for r in pl.itertuples()]
    return photos, CO.session_tasks(pl), load, reads


def _stack_files(root, key):
    return {suffix: root / f"{C.SESSION_STACK_DIR}/{key}{suffix}.parquet" for suffix in ("", "_photos", "_moving")}


def _same_stacks(a, b, tasks):
    for t in tasks:
        fa, fb = _stack_files(a, t["key"]), _stack_files(b, t["key"])
        for s in fa:
            pd.testing.assert_frame_equal(pd.read_parquet(fa[s]), pd.read_parquet(fb[s]))
        ma, mb = CO.read_meta(fa[""].read_bytes()), CO.read_meta(fb[""].read_bytes())
        assert {k: v for k, v in ma.items() if k not in TIMING} == {k: v for k, v in mb.items() if k not in TIMING}
        drop = {"prep_s", "glow_s", "read_wall_s"}
        assert [{k: v for k, v in p.items() if k not in drop} for p in ma["prepare"]] == \
               [{k: v for k, v in p.items() if k not in drop} for p in mb["prepare"]]
        assert mb.get("fused_in_dots") is True and "fused_in_dots" not in ma


def _same_dots(a, b):
    for o in OBS:
        for det in (1, 4):
            for rel in (D.dots_rel(o, det, V2.folder), D.peaks_rel(o, det, V2.folder)):
                pd.testing.assert_frame_equal(pd.read_parquet(a / rel), pd.read_parquet(b / rel))
            ma, mb = D.photo_meta(str(a / D.dots_rel(o, det, V2.folder))), D.photo_meta(str(b / D.dots_rel(o, det, V2.folder)))
            assert ma["dark_holes"] == mb["dark_holes"] and ma["n_partner"] == mb["n_partner"]


def test_fused_worker_writes_what_dots_then_sessionstack_write_reading_each_photo_once(tmp_path, session):
    photos, tasks, load, reads = session
    assert [len(t["obs_ids"]) for t in tasks] == [3, 3] and {t["detector"] for t in tasks} == {1, 4}
    # separate: dots v2, then the step (which reads every photo again)
    a = tmp_path / "separate"
    st = D.process_photos(str(a), photos, prefetch=False)
    assert [s["status"] for s in st] == ["done"] * 6 and not any(s.get("stack") for s in st)
    res = [CO.run_task(str(a), t, loader=lambda u: load(u)) for t in tasks]
    assert [r["status"] for r in res] == ["done", "done"]
    assert len(reads) == 12
    # fused: one worker, each photo read once
    reads.clear()
    b = tmp_path / "fused"
    st = D.process_photos(str(b), photos, prefetch=False, stacks=tasks)
    photo_st = [s for s in st if not s.get("stack")]
    stack_st = [s for s in st if s.get("stack")]
    assert [s["status"] for s in photo_st] == ["done"] * 6 and len(reads) == 6
    assert sorted(s["status"] for s in stack_st) == ["done", "done"] and all(s["fused"] for s in stack_st)
    # partners stay side by side; D1's photos come together, then D4's are interleaved as pairs
    assert [(s["obs_id"], s["detector"]) for s in photo_st] == [(o, d) for o in OBS for d in (1, 4)]
    assert all("stack_cpu_s" in s and "shared_s" in s for s in photo_st)
    _same_stacks(a, b, tasks)
    _same_dots(a, b)


def test_fused_reads_photos_whose_dots_exist_for_their_stack_only(tmp_path, session):
    photos, tasks, load, reads = session
    a = tmp_path / "separate"
    D.process_photos(str(a), photos, prefetch=False)
    for t in tasks:
        CO.run_task(str(a), t, loader=lambda u: load(u))
    b = tmp_path / "resumed"
    shutil.copytree(a / V2.folder, b / V2.folder)  # the dots were done before; the stacks were not
    before = {p.name: p.stat().st_mtime_ns for p in (b / V2.folder).iterdir()}
    reads.clear()
    st = D.process_photos(str(b), photos, prefetch=True, stacks=tasks)
    assert [s["status"] for s in st if not s.get("stack")] == ["skipped"] * 6 and len(reads) == 6
    assert sorted(s["status"] for s in st if s.get("stack")) == ["done", "done"]
    assert {p.name: p.stat().st_mtime_ns for p in (b / V2.folder).iterdir()} == before  # dot files untouched
    _same_stacks(a, b, tasks)


def test_a_missing_or_unreadable_photo_leaves_its_stack_to_the_step(tmp_path, session, monkeypatch):
    photos, tasks, load, reads = session
    st = D.process_photos(str(tmp_path / "part"), photos[:4], prefetch=False, stacks=tasks)  # exposure 3 not here
    assert sorted(s["status"] for s in st if s.get("stack")) == ["incomplete", "incomplete"]
    assert not (tmp_path / "part" / C.SESSION_STACK_DIR).exists()

    def flaky(url):
        if url == f"{OBS[1]}|4":
            raise OSError("network")
        return load(url)

    monkeypatch.setattr(D, "load_photo", flaky)
    monkeypatch.setattr(C, "NET_RETRIES", 1)
    st = D.process_photos(str(tmp_path / "flaky"), photos, prefetch=False, stacks=tasks)
    by = {s["detector"]: s for s in st if s.get("stack")}
    assert by[1]["status"] == "done" and by[4]["status"] == "failed" and "read" in by[4]["error"]
    assert not (tmp_path / "flaky" / CO.session_rel(tasks[1]["key"])).exists()
    assert sum(s["status"] == "failed" for s in st if not s.get("stack")) == 1


def test_off_unless_both_switches(monkeypatch):
    monkeypatch.setattr(C, "SESSION_STACK", False)
    assert not D.fused_stacks(V2)
    monkeypatch.setattr(C, "SESSION_STACK", True)
    assert D.fused_stacks(V2) and not D.fused_stacks(C.DetectSettings())


def test_stack_order_and_session_groups():
    from types import SimpleNamespace
    jobs = [(o, d, "u", s, 1, {}, True) for s, o in ((1, "A"), (1, "B"), (2, "C")) for d in range(1, 7)]
    order = [(j[0], j[1]) for j in D._stack_order(jobs)]
    assert order[:6] == [("A", 1), ("A", 4), ("B", 1), ("B", 4), ("A", 2), ("A", 5)]
    assert order[12:] == [("C", 1), ("C", 4), ("C", 2), ("C", 5), ("C", 3), ("C", 6)]
    rows = [SimpleNamespace(obs_id=o, detector=d, session=s) for s, o in ((1, "A"), (1, "B"), (2, "C"), (3, "D"))
            for d in range(1, 7)]
    assert [len(g) for g in D.session_groups(rows, 4)] == [12, 6, 6]
    assert [len(g) for g in D.session_groups(rows, 13)] == [18, 6]
    lim = D.session_limit(rows, 7)
    assert len({r.session for r in lim}) * 6 <= 18 and all(
        sum(r.session == s for r in lim) == sum(r.session == s for r in rows) for s in {r.session for r in lim})


def test_index_and_report_after_a_fused_run_survive_the_step_finding_everything_written(tmp_path, session):
    from skyshift_pipeline.store import Store, results_rel
    photos, tasks, load, reads = session
    store = Store(str(tmp_path / "r"))
    zone = C.Zone("tz", 0.0, 1.0, 0.0, 1.0)
    st = D.process_photos(store.root, photos, prefetch=False, stacks=tasks)
    rep = CO.write_index_report(store, zone, tasks, [s for s in st if s.get("stack")], 0.0, fused=True)
    assert rep["done"] == 2 and rep["fused_in_dots"]
    idx = store.read_parquet(CO.index_rel(zone.name))
    assert len(idx) == 6 and set(idx["key"]) == {t["key"] for t in tasks}
    # the `sessionstack` step afterwards: everything exists, nothing is read, the report keeps its figures
    reads.clear()
    res = [CO.run_task(store.root, t, loader=load) for t in tasks]
    assert [r["status"] for r in res] == ["exists", "exists"] and reads == []
    rep2 = CO.write_index_report(store, zone, tasks, res, 0.0)
    j = store.read_json(results_rel(zone.name, "session_stack.json"))
    assert rep2["exists"] == 2 and j["fused_in_dots"] and set(j["per_detector"]) == {"D1", "D4"}
    assert len(store.read_parquet(CO.index_rel(zone.name))) == 6
