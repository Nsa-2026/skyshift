import json

import pytest

from skyshift_pipeline.known import dms_to_deg, hms_to_deg, parse_skybot_json, skybot_conesearch

from conftest import SKYBOT_SAMPLE


def test_parse_recorded_sample():
    cols = parse_skybot_json(SKYBOT_SAMPLE.read_text())
    assert len(cols["name"]) == 6
    i = cols["name"].index("Sapientia")
    assert cols["number"][i] == 275
    assert cols["v"][i] == pytest.approx(14.2)
    assert cols["ra"][i] == pytest.approx(hms_to_deg("08 02 49.6382"))
    assert cols["ra"][i] == pytest.approx(120.70682583, abs=1e-6)
    assert cols["dec"][i] == pytest.approx(17.38696, abs=1e-5)
    assert cols["dra_arcsec_h"][i] == pytest.approx(40.8379)
    assert cols["ddec_arcsec_h"][i] == pytest.approx(-8.3384)


def test_parse_empty_and_signs():
    assert parse_skybot_json("")["name"] == []
    assert dms_to_deg("-00 30 00") == pytest.approx(-0.5)
    assert dms_to_deg("+15 09 39.925") == pytest.approx(15.161090, abs=1e-6)


@pytest.mark.network
def test_skybot_live():
    cols = skybot_conesearch(121.6, 15.2, 0.3, 2460965.236)
    assert len(cols["name"]) > 0
