# PROGRESS.md

Update at the end of every session: what was done, what is next, any open problem.

## Status

- [ ] Day 1: τ²-bench + OpenRouter set up, task split saved, harness switches built, $0.50 pilot run
  - [x] τ²-bench cloned and pinned (commit 768f3d8), `uv sync` done, `tau2 check-data` passes (needs PYTHONUTF8=1 on Windows)
  - [x] Task split saved: `configs/tasks.yaml` (tau2's published train/test split = core 30 / held-out 20)
  - [x] Harness switches: `src/harness/levels.py` (H0 = tau2 `llm_agent`; H1-H3 switches defined, agents built Day 2)
  - [x] Runner with dry-run cost estimate, budget guard, append-only `results/runs.csv`, traces in `results/traces/`
  - [x] Unit tests for runner (9 passed); ruff clean
  - [x] **Pilot: Haiku 5.5, H0, 5 core tasks (0, 1, 3, 4, 5), 1 trial**: passed 4/5, total spend $0.0197
        ($0.0165 Anthropic agent + $0.0033 OpenRouter customer). Batch 20261121T180557-haiku-5.5-H0.
        Checked by eye: Haiku 5.5 answered (no temperature sent, no errors); prompt caching works (~75% of agent
        input read from cache); max prompt 11.1K tokens (under Haiku's 100K price step); Mistral customer stays in role.
        Task 3 fail is a real agent failure: DB correct, but the agent never asked for the reservation, so it never
        stated the total of 4 bags, and transferred to a human instead.
  - [x] Pilot DeepSeek V4.1 Flash and Qwen3.6-35B-A3B at H0, same 5 tasks: both 5/5, tool calls work, IDs confirmed.
        Both models think before answering (reasoning tokens in traces). Real per-conversation cost (billed):
        Haiku $0.0041, DeepSeek $0.0020, Qwen $0.0047 (agent + customer).
  - [ ] **OpenRouter backends not pinned**: the customer was served by Venice and DeepInfra in the same batch; Qwen by
        Darkbloom in fp4 (4-bit). Pin one backend per model, then re-pilot all three. Pilot batches so far are NOT study data.
- [ ] Day 2: H1 rules, H2 gates + tests, H3 verifier; Haiku + open-weight H0-H3
- [ ] Day 3: Sonnet H0; analysis; dashboard
- [ ] Day 4: paper draft
- [ ] Extension items (see PRD §5)

## Next step

1. Pin one OpenRouter backend per model (customer, DeepSeek, Qwen) in configs/models.yaml; verify with one call each.
2. Re-run the three 5-task H0 pilots with pinned backends (~$0.06); check traces; then start Day 2.

## Open problems

- Haiku 5.5 runs at the provider default (adaptive thinking, effort medium); LiteLLM returns no thinking blocks or
  reasoning-token counts, so thinking use cannot be seen in traces. Same setting for every level, so the comparison stays fair.
- Pilot rows 1-15 were priced with older cost code (no cache-write premium; OpenRouter at list price without cache
  discount, ~80% too high). Rows are append-only, so they stay; analysis must recompute cost from traces with
  runner.build_row, and exclude pilot batches (20261121T180557, 20261121T184215, 20261121T184407) from study results.
- Credit-error detection (`error=credit`) is a text match on the saved simulation; confirm it on a real credit error.
- H1-H3 agents must be registered with tau2's registry at runtime from our code (never by editing tau2-bench).
