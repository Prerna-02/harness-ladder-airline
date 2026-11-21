# CLAUDE.md: Minimum Viable Harness study

Read this file at the start of every session. Full design, architecture and references live in `@PRD.md`.
Current state of work lives in `PROGRESS.md`: read it first, update it before you end a session.

## What this project is

A controlled experiment: hold the AI model and tasks fixed, add one harness layer at a time
(H0 → H1 → H2 → H3), and measure how task success, rule compliance and cost change.
Environment: τ²-bench **airline** domain (ready-made database, tools, policy, simulated customer, grader).
We build the harness, the runner and the analysis. We do NOT modify τ²-bench's own code or tasks.

Deadline for the core study: **25 November 2026**. Work in the core scope first (see "Scope").

## Golden rules (do not break)

1. **One variable at a time.** Between two conditions, only the harness level (or the one named switch) may differ.
2. **The customer never changes.** Same customer-simulator model, prompt and settings for every run in the study.
3. **Never edit τ²-bench tasks, graders or expected outcomes** to make a run pass. Never delete a failing result.
4. **Write the H2 checks from the policy and the core tasks only.** Never look at held-out tasks while building them.
5. **Every conversation = one row in `results/runs.csv`** (schema in PRD §9) plus its full trace in `results/traces/`.
   Append only. Never overwrite earlier rows; a re-run gets a new `run_id`.
6. **Budget guard.** Two budgets: the OpenRouter key (capped at $10; check `GET https://openrouter.ai/api/v1/key`
   → `limit_remaining`) and the user's Anthropic API credits (track running spend from token counts in
   `results/runs.csv`). Before each batch, estimate its cost per provider. Stop and tell the user if a batch would
   exceed what is left. A conversation cut off by a credit error from either provider is logged with `error=credit`
   and re-run after the top-up. It is never counted as an agent failure.
7. **Secrets.** API keys (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) live only in `.env` (git-ignored).
   Never print them, log them, or commit them.
8. **Pilot before scale.** Any new model, level or task type runs on 5 tasks first; check traces by eye before the full batch.

## Scope

**Core (must finish by the deadline):**
- Models (Claude models via the Anthropic API; open-weight models and the customer simulator via OpenRouter):
  - Claude Haiku 5.5 (cheap, closed): H0, H1, H2, H3
  - DeepSeek V4.1 Flash (cheap, open-weight): H0, H1, H2, H3
  - Qwen3.6-35B-A3B (cheap, open-weight; fallback Qwen3.8-27B with thinking off): H0, H1, H2, H3
  - Claude Sonnet 5.5: H0 only, as the "pay for a better model" reference (15 tasks if credit is tight)
  Anthropic API IDs: `claude-haiku-5-5`, `claude-sonnet-5-5`. Confirm OpenRouter IDs and working tool calls at the pilot.
- Run order: pilot → Haiku → DeepSeek → Qwen → Sonnet.
- Tasks: 30 core airline tasks × 2 trials each.
- Metrics: pass rate, violations, safe completion, cost per safe completion, over-blocking.

**Extension (only after the core is complete and written up):**
- H2 with vs without the rejection reason message.
- 20 held-out tasks; Sonnet with H3; extra cheap models (OpenAI, Gemini) at H0 vs H3.
- Custom compound-request tasks; more trials (pass^k); per-rule-type table; said-vs-did audit.

Design every component so extension runs only **add rows**. No rework.

## Repository layout

```
configs/      models.yaml, tasks.yaml (core / held-out split, fixed seed), budget.yaml
src/harness/  h1_rules.md, h2_gates.py, h3_verify.py, levels.py (builds H0..H3 from switches)
src/          runner.py (loops model × level × task × trial), analyze.py, export_dashboard.py
results/      runs.csv, traces/, figures/
tests/        unit tests for every H2 gate (allowed case + blocked case each)
paper/        drafts and figures for the write-up
```

## How to run

```bash
# All commands run from the repo root inside tau2-bench's environment (Python 3.12); set PYTHONUTF8=1 on Windows.
uv run --project tau2-bench python -m src.runner --model haiku-5.5 --level H2 --tasks core --trials 2 --dry-run  # cost estimate + budget check
uv run --project tau2-bench python -m src.runner --model haiku-5.5 --level H2 --tasks core --trials 2            # real run
uv run --project tau2-bench python -m src.analyze            # rebuilds tables + figures from results/runs.csv
uv run --project tau2-bench python -m src.export_dashboard   # writes the data file for the live results dashboard
uv run --project tau2-bench python -m pytest tests/          # must pass before any batch
```

## Code conventions

- Python 3.11+. Type hints everywhere. Small functions. No notebooks for the pipeline itself.
- Each H2 gate is a pure function: `(proposed_tool_call, db_state, conversation) -> Allow | Block(reason)`.
  Gates are read-only and deterministic. Each gate has a unit test for one allowed and one blocked case.
- Every harness feature sits behind a named switch in `levels.py` so any level can be rebuilt exactly.
- Fixed seeds; record model ID, temperature and τ²-bench commit hash in every row.
- Call all models through LiteLLM: `anthropic/claude-haiku-5-5` and `anthropic/claude-sonnet-5-5` (Anthropic API),
  `openrouter/<provider>/<model>` for everything else. Record the provider in every row.
- Prompt caching is the biggest cost saver. For Claude models, mark the fixed prefix (system prompt, policy, tool
  definitions) with `cache_control`; it must be identical across turns or the cache misses.

## How we work in sessions

- One unit of work per session (e.g. "build H2 gates" or "run Haiku H0-H3"). Commit before starting the next.
- Use Claude Opus 5.5 at high effort for design, gate code, debugging and writing; Sonnet 5.5 at medium for
  routine runs, charts and formatting.
- Use `/clear` between unrelated units of work and `/compact` in long sessions.
- When a mistake repeats, add a rule here so it cannot happen again.

## Recommended plugins (official marketplace)

```
/plugin install pyright-lsp@claude-plugins-official        # needs: npm i -g pyright
/plugin install commit-commands@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
```
- `pyright-lsp`: Python type errors reported after every edit, and precise code navigation instead of broad search.
- `commit-commands`: consistent commits for each unit of work.
- `security-guidance`: reviews changes for issues like leaked keys.

## Change log (update in the same commit as any decision change)

- 2026-11-21: Study design fixed: airline domain, H0-H3 ladder, core vs extension scope, OpenRouter for all models.
- 2026-11-21: Core uses two open-weight models (DeepSeek V4.1 Flash and Qwen3.6-35B-A3B). OpenRouter key capped at $10;
  top up when the budget guard says so. Runs go cheapest first.
- 2026-11-21: Claude models (Haiku 5.5, Sonnet 5.5) run on the user's Anthropic API credits, not OpenRouter.
  OpenRouter now covers only DeepSeek, Qwen and the customer simulator.
- 2026-11-21 (Day 1): tau2-bench pinned at commit 768f3d8 (cloned into tau2-bench/, git-ignored; own .venv, Python 3.12).
  Task split = tau2's published airline split (train 30 -> core, test 20 -> held-out) instead of a new seeded draw:
  same 30/20 sizes, fixed and public. COMMUNICATE grading uses tau2's deterministic substring scorer, not its default
  GPT-5.5 judge (no OpenAI key, no cost; affects core tasks 3, 7, 11, 14, 23 only).
  Claude 5.5 models get no temperature (non-default values return HTTP 400); open-weight models and the customer use 0.0.
  Cost is computed from token counts x configs/models.yaml prices (LiteLLM has no prices for the OpenRouter models).
  runs.csv adds columns provider, temperature, user_model_id, user_cost_usd, termination_reason to PRD §11.
  OpenRouter prices on 2026-11-21: DeepSeek V4.1 Flash $0.05/$0.199, Qwen3.6-35B-A3B $0.10/$0.90 (PRD §9 table is older).
- 2026-11-21: Customer simulator fixed for the whole study: openrouter/mistralai/mistral-small-3.2-24b-instruct,
  temperature 0 (a family never used as an agent, including the extension). Approved by the user.
- 2026-11-22: OpenRouter backends pinned, one endpoint per model, no fallback (DeepSeek baidu/fp8, Qwen parasail/fp8,
  customer mistral/zdr): OpenRouter otherwise switches hosts and precision (Qwen was served fp4) between calls.
  Every model has max_tokens (agents 8192, customer 1024) and a 180 s timeout per call.
- 2026-11-22: Customer simulator changed, before any study run, to openrouter/mistralai/mistral-small-2603 served by
  Mistral, temperature 0: Mistral Small 3.2 fell into repetition loops (2 of 10 pilot conversations). Approved by the user.
  A conversation where a customer reply hits the length cap gets error=customer_loop: excluded and re-run, like
  error=credit, never counted as an agent failure.
- 2026-11-22: Cost of OpenRouter calls = billed usage.cost from the response; Anthropic calls are token-priced.
  Pilot batches (all of 2026-11-21/22 before the final pilot) are rehearsal data and are excluded from study results.
