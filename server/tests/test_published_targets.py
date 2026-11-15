"""The site asks the science endpoints at its own target positions (web/src/data/targets.json):
every featured science field must answer there, and a nearby but different spot must not."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skyshift_server import published_science
from skyshift_server.app import _repo_root, app

SCIENCE = ["barnard", "epsindib", "wise0855", "rwaur", "rdra"]


def _targets() -> dict[str, dict]:
    path = Path(_repo_root()) / "web" / "src" / "data" / "targets.json"
    return {t["slug"]: t for t in json.loads(path.read_text())["targets"]}


@pytest.mark.parametrize("slug", SCIENCE)
def test_site_target_position_finds_its_published_field(slug):
    t = _targets()[slug]
    folder = published_science.field(t["ra"], t["dec"])
    assert folder is not None and folder.name == slug
    assert published_science.read(t["ra"], t["dec"], "science") is not None


def test_a_spot_half_an_arcminute_away_is_not_relabelled():
    t = _targets()["epsindib"]
    assert published_science.field(t["ra"], t["dec"] + 30 / 3600) is None


def test_exports_and_analysis_answer_at_the_site_position_of_epsilon_indi_b(monkeypatch):
    monkeypatch.delenv("SKYSHIFT_ENABLE_SCIENCE_JOBS", raising=False)
    t = _targets()["epsindib"]
    client = TestClient(app)
    science = client.get("/api/science", params={"ra": t["ra"], "dec": t["dec"]})
    assert science.status_code == 200
    export = client.get("/api/export", params={"ra": t["ra"], "dec": t["dec"], "det": 2, "clock": "months",
                                               "format": "csv"})
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
