"""Unit tests for the runner's cost, row and split logic. No API calls."""

import pytest

from src import runner
from src.harness.levels import LEVELS, Switches, tau2_agent_name

AGENT = {"litellm_id": "anthropic/claude-haiku-5-5", "provider": "anthropic",
         "price_in": 0.10, "price_cached_in": 0.01, "price_out": 0.50, "llm_args": {}}
USER = {"litellm_id": "openrouter/x/y", "provider": "openrouter",
        "price_in": 0.075, "price_cached_in": 0.075, "price_out": 0.20, "llm_args": {"temperature": 0.0}}


def test_cost_prices_cached_tokens_separately():
    # 1M input of which 400k cached, 100k output
    assert runner.cost_usd(1_000_000, 400_000, 100_000, AGENT) == pytest.approx(0.06 + 0.004 + 0.05)


def test_cache_writes_cost_a_quarter_more():
    # 10k input tokens, all written to cache: billed at 1.25x the input price
    assert runner.cost_usd(10_000, 0, 0, AGENT, tokens_cache_written=10_000) == pytest.approx(10_000 * 0.10 * 1.25 / 1e6)


def test_estimate_splits_by_provider():
    est = {"agent_in": 1_000_000, "agent_out": 0, "user_in": 1_000_000, "user_out": 0}
    assert runner.estimate_batch(2, AGENT, USER, est) == pytest.approx({"anthropic": 0.2, "openrouter": 0.15})


def _sim(termination="user_stop", reward=1.0):
    return {
        "task_id": "3", "trial": 0, "seed": 300, "timestamp": "t", "duration": 12.5,
        "termination_reason": termination, "reward_info": {"reward": reward},
        "messages": [
            {"role": "user", "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
            {"role": "assistant", "usage": {"prompt_tokens": 1000, "completion_tokens": 50},
             "raw_data": {"usage": {"prompt_tokens_details": {"cached_tokens": 800}}}},
            {"role": "tool", "content": "{}"},
        ],
    }


def test_build_row_counts_agent_and_user_tokens_apart():
    row = runner.build_row(_sim(), batch_id="b", agent_cfg=AGENT, user_cfg=USER, level="H0",
                           split="core", tau2_commit="abc")
    assert set(row) == set(runner.COLUMNS)
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (1000, 800, 50)
    assert row["passed"] is True and row["error"] == "" and row["n_turns"] == 3
    assert row["temperature"] == "provider-default"
    assert row["user_cost_usd"] == pytest.approx((100 * 0.075 + 10 * 0.20) / 1e6)


def test_openrouter_cost_uses_billed_amount():
    sim = _sim()
    sim["messages"][0]["raw_data"] = {"usage": {"cost": 0.0042}}  # user side is on OpenRouter
    row = runner.build_row(sim, batch_id="b", agent_cfg=AGENT, user_cfg=USER, level="H0",
                           split="core", tau2_commit="abc")
    assert row["user_cost_usd"] == pytest.approx(0.0042)
    # Anthropic side prices tokens: 200 uncached + 800 cached in, 50 out
    assert row["cost_usd"] == pytest.approx((200 * 0.10 + 800 * 0.01 + 50 * 0.50) / 1e6)


def test_credit_error_is_not_an_agent_failure():
    sim = _sim(termination="agent_error", reward=0.0)
    sim["messages"].append({"role": "assistant", "content": "Error 402: requires more credits"})
    assert runner.classify_error(sim) == "credit"
    assert runner.classify_error(_sim(termination="agent_error", reward=0.0)) == "agent_error"


def test_append_rows_refuses_duplicate_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_CSV", tmp_path / "runs.csv")
    row = runner.build_row(_sim(), batch_id="b", agent_cfg=AGENT, user_cfg=USER, level="H0",
                           split="core", tau2_commit="abc")
    runner.append_rows([row])
    with pytest.raises(ValueError):
        runner.append_rows([row])
    assert len(runner.read_rows()) == 1


def test_select_tasks_rejects_ids_outside_split():
    cfg = {"core": ["0", "1", "3"], "held_out": ["2"]}
    assert runner.select_tasks(cfg, "core", 2, None) == ["0", "1"]
    with pytest.raises(ValueError):
        runner.select_tasks(cfg, "core", None, ["2"])  # held-out task must never leak into core runs


def test_levels_are_cumulative():
    def on(s: Switches) -> set[str]:
        return {k for k, v in s.as_dict().items() if v and k != "gate_reason"}

    assert on(LEVELS["H0"]) == set()
    assert on(LEVELS["H1"]) < on(LEVELS["H2"]) < on(LEVELS["H3"])
    assert tau2_agent_name("H0") == "llm_agent"
    assert LEVELS["H0"] == Switches()
