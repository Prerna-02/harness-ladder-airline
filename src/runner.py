"""Experiment runner: one model x one harness level x a task split x N trials.

Every conversation becomes one row in results/runs.csv (append only) and its full trace is kept in
results/traces/<batch_id>.json (tau2's own results file). Cost: OpenRouter calls use the billed usage.cost from
the response; Anthropic calls are priced from token counts and configs/models.yaml (LiteLLM has no OpenRouter prices).

Run from the repo root inside tau2's environment:
    uv run --project tau2-bench python -m src.runner --model haiku-5.5 --level H0 --tasks core --limit 5 --trials 1 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.harness.levels import LEVELS, tau2_agent_name

ROOT = Path(__file__).resolve().parent.parent
RUNS_CSV = ROOT / "results" / "runs.csv"
TRACES_DIR = ROOT / "results" / "traces"

# PRD section 11, plus provider / temperature / customer-simulator columns required by CLAUDE.md conventions.
COLUMNS = [
    "run_id", "timestamp", "model_id", "provider", "temperature", "harness_level", "gate_reason_on",
    "task_id", "split", "trial", "seed", "tau2_commit", "user_model_id",
    "reward", "passed", "n_violations", "violated_rules", "n_blocks", "n_false_blocks", "said_vs_did_flag",
    "input_tokens", "cached_tokens", "output_tokens", "cost_usd", "user_cost_usd",
    "latency_s", "n_turns", "termination_reason", "error",
]
ERROR_TERMINATIONS = {"too_many_errors", "agent_error", "user_error", "infrastructure_error",
                      "context_window_exceeded", "unexpected_error"}
CREDIT_MARKERS = ("insufficient credit", "insufficient_quota", "credit balance", "402", "requires more credits")


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))


# ---------- cost ----------

CACHE_WRITE_PREMIUM = 0.25  # Anthropic bills 5-minute cache writes at 1.25x the input price


def cost_usd(tokens_in: int, tokens_cached: int, tokens_out: int, model_cfg: dict,
             tokens_cache_written: int = 0) -> float:
    """Price one model's tokens. Cached-read and cache-written tokens are both subsets of tokens_in."""
    uncached = tokens_in - tokens_cached
    return (uncached * model_cfg["price_in"] + tokens_cached * model_cfg["price_cached_in"]
            + tokens_cache_written * model_cfg["price_in"] * CACHE_WRITE_PREMIUM
            + tokens_out * model_cfg["price_out"]) / 1e6


def estimate_batch(n_conversations: int, agent_cfg: dict, user_cfg: dict, est: dict) -> dict[str, float]:
    """Pre-run cost estimate per provider (no caching assumed, so it errs high)."""
    spend: dict[str, float] = {}
    agent = n_conversations * cost_usd(est["agent_in"], 0, est["agent_out"], agent_cfg)
    user = n_conversations * cost_usd(est["user_in"], 0, est["user_out"], user_cfg)
    spend[agent_cfg["provider"]] = spend.get(agent_cfg["provider"], 0.0) + agent
    spend[user_cfg["provider"]] = spend.get(user_cfg["provider"], 0.0) + user
    return spend


# ---------- budget guard (golden rule 6) ----------

def openrouter_remaining() -> float | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)["data"]
    return data.get("limit_remaining")


def anthropic_spent(rows: list[dict]) -> float:
    return sum(float(r["cost_usd"] or 0) for r in rows if r["provider"] == "anthropic")


def read_rows() -> list[dict]:
    if not RUNS_CSV.exists():
        return []
    with RUNS_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def check_budget(estimate: dict[str, float], budget: dict) -> list[str]:
    """Return a list of problems; empty means the batch fits."""
    problems = []
    if "openrouter" in estimate:
        remaining = openrouter_remaining()
        if remaining is None:
            problems.append("OpenRouter: no OPENROUTER_API_KEY or no key limit set, cannot check remaining credit")
        elif estimate["openrouter"] > remaining:
            problems.append(f"OpenRouter: estimate ${estimate['openrouter']:.2f} > remaining ${remaining:.2f}")
    if "anthropic" in estimate:
        credit = budget.get("anthropic_credit_usd")
        if credit is None:
            problems.append("Anthropic: set anthropic_credit_usd in configs/budget.yaml")
        else:
            left = credit - anthropic_spent(read_rows())
            if estimate["anthropic"] > left:
                problems.append(f"Anthropic: estimate ${estimate['anthropic']:.2f} > left ${left:.2f}")
    return problems


# ---------- one conversation -> one row ----------

def _side(messages: list[dict], role: str, cfg: dict) -> tuple[int, int, int, float]:
    """Sum (input tokens, cache-read input tokens, output tokens, cost USD) over one side of the conversation.

    OpenRouter returns the amount it actually billed in usage.cost (cache discounts included), so that is used
    when present; otherwise the call is priced from its tokens and configs/models.yaml.
    """
    tin = tcached = tout = 0
    usd = 0.0
    for m in messages:
        if m.get("role") != role or not m.get("usage"):
            continue
        p_in = m["usage"].get("prompt_tokens") or 0
        p_out = m["usage"].get("completion_tokens") or 0
        raw_usage = (m.get("raw_data") or {}).get("usage") or {}
        details = raw_usage.get("prompt_tokens_details") or {}
        p_cached = details.get("cached_tokens") or raw_usage.get("cache_read_input_tokens") or 0
        billed = raw_usage.get("cost")
        if cfg["provider"] == "openrouter" and isinstance(billed, (int, float)):
            usd += billed
        else:
            usd += cost_usd(p_in, p_cached, p_out, cfg, raw_usage.get("cache_creation_input_tokens") or 0)
        tin, tcached, tout = tin + p_in, tcached + p_cached, tout + p_out
    return tin, tcached, tout, usd


def _customer_hit_cap(sim: dict) -> bool:
    """A customer reply cut off by max_tokens means the simulator broke (e.g. a repetition loop), not the agent."""
    for m in sim.get("messages") or []:
        choices = (m.get("raw_data") or {}).get("choices") or [{}]
        if m.get("role") == "user" and choices[0].get("finish_reason") == "length":
            return True
    return False


def _harness_count(messages: list[dict], kind: str) -> int:
    """How many harness events of this kind (block, verify, fallback) the agent's messages recorded."""
    return sum(1 for m in messages for e in ((m.get("raw_data") or {}).get("harness") or {}).get("events", [])
               if e.get("type") == kind)


def classify_error(sim: dict) -> str:
    """'' for a clean conversation. 'credit' and 'customer_loop' rows are excluded and re-run, never scored."""
    reason = sim.get("termination_reason") or ""
    # shortcut: credit errors are found by text match on the saved simulation; upgrade if a provider words it differently
    if reason in ERROR_TERMINATIONS and any(k in json.dumps(sim).lower() for k in CREDIT_MARKERS):
        return "credit"
    if _customer_hit_cap(sim):
        return "customer_loop"
    return reason if reason in ERROR_TERMINATIONS else ""


def build_row(sim: dict, *, batch_id: str, agent_cfg: dict, user_cfg: dict, level: str,
              split: str, tau2_commit: str) -> dict[str, Any]:
    messages = sim.get("messages") or []
    a_in, a_cached, a_out, a_usd = _side(messages, "assistant", agent_cfg)
    _, _, _, u_usd = _side(messages, "user", user_cfg)
    reward = (sim.get("reward_info") or {}).get("reward")
    return {
        "run_id": f"{batch_id}-task{sim['task_id']}-trial{sim.get('trial')}",
        "timestamp": sim.get("timestamp"),
        "model_id": agent_cfg["litellm_id"],
        "provider": agent_cfg["provider"],
        "temperature": agent_cfg["llm_args"].get("temperature", "provider-default"),
        "harness_level": level,
        "gate_reason_on": LEVELS[level].gate_reason if LEVELS[level].code_gates else "",
        "task_id": sim["task_id"],
        "split": split,
        "trial": sim.get("trial"),
        "seed": sim.get("seed"),
        "tau2_commit": tau2_commit,
        "user_model_id": user_cfg["litellm_id"],
        "reward": reward,
        "passed": reward == 1.0,
        # Filled later by the trace audits (violations, over-blocking, said-vs-did).
        "n_violations": "", "violated_rules": "", "n_false_blocks": "", "said_vs_did_flag": "",
        "n_blocks": _harness_count(messages, "block"),
        "input_tokens": a_in,
        "cached_tokens": a_cached,
        "output_tokens": a_out,
        "cost_usd": a_usd,
        "user_cost_usd": u_usd,
        "latency_s": sim.get("duration"),
        "n_turns": len(messages),
        "termination_reason": sim.get("termination_reason"),
        "error": classify_error(sim),
    }


def append_rows(rows: list[dict]) -> None:
    """Append only (golden rule 5): never rewrite earlier rows."""
    RUNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = {r["run_id"] for r in read_rows()}
    clash = existing_ids & {r["run_id"] for r in rows}
    if clash:
        raise ValueError(f"run_id already in runs.csv, refusing to overwrite: {sorted(clash)[:3]}")
    new_file = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


# ---------- main ----------

def select_tasks(tasks_cfg: dict, split: str, limit: int | None, task_ids: list[str] | None) -> list[str]:
    ids = tasks_cfg[split]
    if task_ids:
        unknown = set(task_ids) - set(ids)
        if unknown:
            raise ValueError(f"task ids not in split '{split}': {sorted(unknown)}")
        ids = [i for i in ids if i in task_ids]
    return ids[:limit] if limit else ids


def run_batch(task_ids: list[str], level: str, agent_cfg: dict, user_cfg: dict, tasks_cfg: dict,
              trials: int, save_path: Path, max_concurrency: int) -> list[dict]:
    # Imported here so --dry-run and the unit tests do not need tau2 to load its data.
    from tau2.data_model.semantic_communication import CommunicateScoring
    from tau2.data_model.simulation import TextRunConfig
    from tau2.run import get_tasks
    from tau2.runner import run_tasks

    from src.harness.agent import register_levels
    register_levels()
    config = TextRunConfig(
        domain=tasks_cfg["domain"],
        agent=tau2_agent_name(level),
        user="user_simulator",
        llm_agent=agent_cfg["litellm_id"],
        llm_args_agent=agent_cfg["llm_args"],
        llm_user=user_cfg["litellm_id"],
        llm_args_user=user_cfg["llm_args"],
        num_trials=trials,
        seed=tasks_cfg["seed"],
        max_concurrency=max_concurrency,
        communicate_scoring=CommunicateScoring(scorer=tasks_cfg["communicate_scorer"]),
    )
    tasks = get_tasks(tasks_cfg["domain"], task_ids=task_ids)
    run_tasks(config, tasks, save_path=save_path)
    return json.loads(save_path.read_text(encoding="utf-8"))["simulations"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, help="key under agents: in configs/models.yaml")
    p.add_argument("--level", required=True, choices=sorted(LEVELS))
    p.add_argument("--tasks", default="core", choices=["core", "held_out"])
    p.add_argument("--trials", type=int, default=2)
    p.add_argument("--limit", type=int, help="only the first N tasks of the split (pilot)")
    p.add_argument("--task-ids", nargs="+")
    p.add_argument("--max-concurrency", type=int, default=3)
    p.add_argument("--dry-run", action="store_true", help="print the cost estimate and budget check only")
    args = p.parse_args(argv)

    load_dotenv(ROOT / ".env")
    models, tasks_cfg, budget = load_yaml("models.yaml"), load_yaml("tasks.yaml"), load_yaml("budget.yaml")
    agent_cfg, user_cfg = models["agents"][args.model], models["user_simulator"]
    task_ids = select_tasks(tasks_cfg, args.tasks, args.limit, args.task_ids)
    n_conv = len(task_ids) * args.trials

    estimate = estimate_batch(n_conv, agent_cfg, user_cfg, budget["estimate_tokens_per_conversation"])
    print(f"Batch: {args.model} {args.level} {args.tasks} tasks={task_ids} trials={args.trials} -> {n_conv} conversations")
    for provider, usd in estimate.items():
        print(f"  estimate {provider}: ${usd:.3f}")
    problems = check_budget(estimate, budget)
    for msg in problems:
        print(f"  BUDGET: {msg}")
    if args.dry_run:
        return 0
    if problems:
        print("Stopping: budget guard failed. Tell the user before running.")
        return 1

    batch_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{args.model}-{args.level}"
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    sims = run_batch(task_ids, args.level, agent_cfg, user_cfg, tasks_cfg, args.trials,
                     TRACES_DIR / f"{batch_id}.json", args.max_concurrency)
    rows = [build_row(s, batch_id=batch_id, agent_cfg=agent_cfg, user_cfg=user_cfg,
                      level=args.level, split=args.tasks, tau2_commit=tasks_cfg["tau2_commit"]) for s in sims]
    append_rows(rows)
    spent = sum(r["cost_usd"] + r["user_cost_usd"] for r in rows)
    passed = sum(r["passed"] for r in rows)
    print(f"Done: {len(rows)} rows -> {RUNS_CSV}. passed {passed}/{len(rows)}, spend ${spent:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
