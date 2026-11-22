"""analyze.py: confidence interval, study-batch selection and failure labels."""

import pandas as pd
import pytest

from src import analyze as an


def test_wilson_interval():
    lo, hi = an.wilson(45, 60)
    assert lo == pytest.approx(0.628, abs=0.002) and hi == pytest.approx(0.842, abs=0.002)
    assert an.wilson(0, 10)[0] == 0.0 and an.wilson(10, 10)[1] == 1.0


def _rows(batch, model, level, tasks, trials):
    return [{"run_id": f"{batch}-task{t}-trial{k}", "model_id": model, "harness_level": level, "task_id": t,
             "split": "core", "trial": str(k)} for t in tasks for k in range(trials)]


def test_only_complete_core_batches_count():
    core = ["0", "1", "3"]
    runs = pd.DataFrame(_rows("20261121T000000-m-H0", "m", "H0", ["0", "1"], 1)       # pilot: partial
                        + _rows("20261122T000000-m-H0", "m", "H0", core, 2)           # full batch
                        + _rows("20261122T090000-m-H1", "m", "H1", core, 1)           # wrong trial count
                        + _rows("20261122T100000-s-H0", "s", "H0", ["0", "3"], 2))    # the fixed half-set
    assert an.study_batches(runs, [core, ["0", "3"]]) == {("m", "H0"): "20261122T000000-m-H0",
                                                          ("s", "H0"): "20261122T100000-s-H0"}


@pytest.mark.parametrize("fields, label", [
    ({"passed": True}, "pass"),
    ({"rule_violations": 1, "unexpected_writes": 1}, "broke a rule"),
    ({"unexpected_writes": 2}, "wrong or unrequested change"),
    ({"missed_writes": 1}, "missed a needed change"),
    ({"db_ok": True}, "did not tell the customer"),
    ({}, "other"),
])
def test_failure_type(fields, label):
    base = {"passed": False, "rule_violations": 0, "unexpected_writes": 0, "missed_writes": 0, "db_ok": False}
    assert an.failure_type(pd.Series({**base, **fields})) == label
