"""rb_train.py: the exported trees predict what sklearn predicts; the held-out measures. Needs scikit-learn:
cd pipeline && uv run --with scikit-learn pytest ../scripts/vet/tests/test_rb_train.py"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rb_train as T  # noqa: E402
from skyshift_pipeline import rb_score as RB  # noqa: E402


def test_exported_trees_match_sklearn():
    sk = pytest.importorskip("sklearn.ensemble")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, len(RB.FEATURES)))
    X[rng.random(X.shape) < 0.1] = np.nan                  # missing values go the trees' learned way
    y = ((np.nan_to_num(X[:, 0]) + 0.5 * np.nan_to_num(X[:, 3]) + rng.normal(0, 0.5, 600)) > 0).astype(int)
    m = sk.HistGradientBoostingClassifier(max_iter=20, max_leaf_nodes=6, random_state=0).fit(X, y)
    model = T.export_trees(m, list(RB.FEATURES))
    assert model["features"] == list(RB.FEATURES) and len(model["trees"]) == 20
    assert np.max(np.abs(RB.predict(model, X) - m.predict_proba(X)[:, 1])) < 1e-5


def test_top_real_and_ranks_leave_exempt_out():
    info = pd.DataFrame({"zone": ["z"] * 5, "id": list("abcde"), "grade": ["B"] * 5, "label": [1, 0, 1, 0, 1],
                         "exempt": [False, False, False, False, True], "rb": [0.9, 0.1, 0.8, 0.2, 0.0],
                         "base": [0.1, 0.9, 0.2, 0.8, 1.0], "name": [""] * 5})
    t = T.top_real(info, "rb")["B"]["top10"]
    assert t == {"real": 2, "rest": 2}
    assert T.top_real(info, "rb")["B"]["top10"] == T.top_real(info, "base")["B"]["top10"]  # same set of 4
    info10 = info.copy()
    T.TOP_N, old = (2,), T.TOP_N
    try:
        assert T.top_real(info, "rb")["B"]["top2"] == {"real": 2, "rest": 0}
        assert T.top_real(info, "base")["B"]["top2"] == {"real": 0, "rest": 2}
    finally:
        T.TOP_N = old
    (r,) = T.ranks(info10, ["a"])
    assert (r["rank_existing"], r["rank_rb"], r["of"]) == (4, 1, 4)
