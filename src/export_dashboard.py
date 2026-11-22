"""Write the live dashboard's data files from the analysis tables (run `python -m src.analyze` first).

Outputs results/dashboard/: summary.csv (one row per model x level), tasks.csv (passes per task),
progress.csv (planned core conditions and whether each has study data). The findings log is
results/findings.csv, written by hand as findings come in, and is uploaded as it is.

    uv run --project tau2-bench python -m src.export_dashboard
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = RESULTS / "dashboard"

NAMES = {
    "anthropic/claude-haiku-5-5": "Haiku 5.5",
    "anthropic/claude-sonnet-5-5": "Sonnet 5.5",
    "openrouter/deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "openrouter/qwen/qwen3.6-35b-a3b": "Qwen3.6-35B-A3B",
}
# Core plan (CLAUDE.md "Scope"): cheap models at every level, Sonnet at H0 only as the reference.
PLANNED = [(m, lvl) for m in ("Haiku 5.5", "DeepSeek V4.1 Flash", "Qwen3.6-35B-A3B") for lvl in ("H0", "H1", "H2", "H3")]
PLANNED += [("Sonnet 5.5", "H0")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = pd.read_csv(RESULTS / "summary.csv")
    runs = pd.read_csv(RESULTS / "derived_runs.csv", dtype={"task_id": str})
    summary["model"] = summary.model_id.map(NAMES).fillna(summary.model_id)
    summary["condition"] = summary.model + " · " + summary.harness_level
    keep = ["condition", "model", "harness_level", "provider", "n_tasks", "n_scored", "n_excluded", "passed", "pass_rate",
            "pass_ci_low", "pass_ci_high", "pass_hat_2", "safe_completions", "safe_rate", "rule_violations",
            "unexpected_writes", "missed_writes", "blocks", "over_blocks", "agent_cost_usd", "customer_cost_usd",
            "cost_per_conversation", "cost_per_safe_completion", "avg_input_tokens", "avg_output_tokens", "avg_turns",
            "avg_latency_s"] + [c for c in summary.columns if c.startswith("fail_")]
    summary[keep].round(6).to_csv(OUT / "summary.csv", index=False)

    runs["model"] = runs.model_id.map(NAMES).fillna(runs.model_id)
    tasks = (runs.groupby(["model", "harness_level", "task_id"])
             .agg(passes=("passed", "sum"), trials=("passed", "size")).reset_index())
    tasks["passes"] = tasks.passes.astype(int)
    tasks.to_csv(OUT / "tasks.csv", index=False)

    done = set(zip(summary.model, summary.harness_level))
    progress = pd.DataFrame([{"model": m, "harness_level": lvl, "status": "done" if (m, lvl) in done else "pending",
                              "conversations": int(summary[(summary.model == m) & (summary.harness_level == lvl)].n_scored.sum())}
                             for m, lvl in PLANNED])
    progress.to_csv(OUT / "progress.csv", index=False)
    print(f"Wrote {OUT}: summary.csv ({len(summary)} conditions), tasks.csv ({len(tasks)} rows), "
          f"progress.csv ({int((progress.status == 'done').sum())}/{len(progress)} done)")


if __name__ == "__main__":
    main()
