# PROGRESS.md

Update at the end of every session: what was done, what is next, any open problem.

## Status

- [ ] Day 1: τ²-bench + OpenRouter set up, task split saved, harness switches built, $0.50 pilot run
  - [x] τ²-bench cloned and pinned (commit 768f3d8), `uv sync` done, `tau2 check-data` passes (needs PYTHONUTF8=1 on Windows)
  - [x] Task split saved: `configs/tasks.yaml` (tau2's published train/test split = core 30 / held-out 20)
  - [x] Harness switches: `src/harness/levels.py` (H0 = tau2 `llm_agent`; H1-H3 switches defined, agents built Day 2)
  - [x] Runner with dry-run cost estimate, budget guard, append-only `results/runs.csv`, traces in `results/traces/`
  - [x] Unit tests for runner (7 passed); ruff clean
  - [ ] **Pilot: Haiku 5.5, H0, 5 core tasks, 1 trial** (dry-run estimate ≈ $0.09 Anthropic + $0.02 OpenRouter). Blocked on the items below.
- [ ] Day 2: H1 rules, H2 gates + tests, H3 verifier; Haiku + open-weight H0-H3
- [ ] Day 3: Sonnet H0; analysis; dashboard
- [ ] Day 4: paper draft
- [ ] Extension items (see PRD §5)

## Next step

1. User: copy `env.example` to `.env` and fill in both keys; set `anthropic_credit_usd` in `configs/budget.yaml`.
2. Done: customer simulator approved (Mistral Small 3.2 24B via OpenRouter, temperature 0).
3. Dry run, then the pilot:
   `uv run --project tau2-bench python -m src.runner --model haiku-5.5 --level H0 --tasks core --limit 5 --trials 1`
4. Read the 5 traces by eye; check that the Haiku call works with no temperature, that caching shows `cached_tokens > 0`,
   and replace the token estimates in `configs/budget.yaml` with pilot averages.
5. Pilot DeepSeek and Qwen at H0 on the same 5 tasks to confirm OpenRouter IDs and tool calling.

## Open problems

- Not yet verified by a real call: LiteLLM 1.81 accepts `anthropic/claude-haiku-5-5`; `cache_control_injection_points` caches the policy prompt;
  Claude 5.5 runs with adaptive thinking at default effort (medium), which adds output tokens. Decide at the pilot whether to fix effort explicitly.
- Haiku 5.5 price is $0.10/$0.50 only up to 100K-token prompts; check per-call prompt sizes in the pilot traces.
- Credit-error detection (`error=credit`) is a text match on the saved simulation; confirm it on a real credit error.
- H1-H3 agents must be registered with tau2's registry at runtime from our code (never by editing tau2-bench).
