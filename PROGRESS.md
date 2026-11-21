# PROGRESS.md

Update at the end of every session: what was done, what is next, any open problem.

## Status

- [x] Day 1: τ²-bench + OpenRouter set up, task split saved, harness switches built, $0.50 pilot run
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
  - [x] OpenRouter backends pinned (customer deepinfra/fp8, DeepSeek baidu/fp8, Qwen parasail/fp8, no fallback);
        verified with one call each (served host and billed price match the pinned endpoint).
  - [x] Re-pilot 1 (pinned, no caps) stopped after 45 min: the customer looped in Haiku task 0 (~$0.04 extra OpenRouter
        output). 4 finished conversations salvaged as rows (batch 20261121T202804); task 0 conversation lost.
  - [x] Caps added to every model: max_tokens (agents 8192, customer 1024) + 180 s timeout per call.
  - [x] Re-pilot 2 (pinned + caps): Haiku 4/5, DeepSeek 4/5, Qwen 5/5; no hangs. BUT the customer (Mistral Small 3.2 on
        DeepInfra) looped again in Haiku task 5: 12 replies hit the 1024 cap repeating "I'm a Gold member, I know I am...",
        and the task failed because of the customer. 0 loops in 15 earlier conversations served mostly by Venice.
  - [x] Customer switched to Mistral Small 2603 (mistral/zdr, temp 0, cap 1024); conversations where a customer reply
        hits the cap are flagged error=customer_loop (excluded and re-run). 10 tests pass.
  - [x] **Final H0 pilot** (batches 20261121T222916 Haiku, 20261121T223042 DeepSeek, 20261121T223232 Qwen): 15/15 passed,
        0 customer loops (max customer reply 90 tokens), every call served by its pinned host. Spend $0.078
        (Haiku $0.0194, DeepSeek $0.0049, Qwen $0.0535). Tasks 0-5 look easy at H0 (ceiling); the full 30 will spread more.
        Customer quirk seen: it sometimes says "I already gave you my user ID" before it has. Harmless so far; watch it.
  - [x] docs/call-replay.html: animated replay of task 3 for all three agents (also published as a private artifact).
- [ ] Day 2: H1 rules, H2 gates + tests, H3 verifier; Haiku + open-weight H0-H3
  - [x] H1 rules (src/harness/h1_rules.md) + checklist restated in the confirmation message (tau2's `think` tool is
        disabled in this commit, so the checklist goes in the message). H2: 10 gates (src/harness/h2_gates.py).
        H3: explicit-yes check + verifier call (src/harness/h3_verify.py). HarnessAgent registered as harness_H1..H3.
        57 tests pass; every expected write in the 30 core tasks passes all gates (no over-blocking on the answer key).
  - [x] Level pilot, Haiku, core tasks 5/7/12/39/40, 1 trial (batches 20261121T224909..225305): H0 2/5, H1 1/5,
        H2 2/5, H3 2/5; $0.144 total. Agents registered correctly; H3 verifier ran on every write (all OK); 0 blocks
        and 0 confirm rejections. Replaying all 20 conversations' writes through the gates: none would be blocked.
        Failures were omissions (tasks 12, 39: needed write not done) and one customer going off-script (task 7: the
        customer asked to cancel two extra reservations its script never mentions, and said yes).
  - [ ] Haiku H0-H3 core batches (30 tasks x 2 trials, 240 conversations)
- [ ] Day 3: Sonnet H0; analysis; dashboard
- [ ] Day 4: paper draft
- [ ] Extension items (see PRD §5)

## Next step

Day 2: write H1 rules (src/harness/h1_rules.md) and H2 gates + one allowed/one blocked unit test each, from the policy and
core tasks only; H3 confirm/verify; register H1-H3 agents with tau2 at runtime; 5-task pilot per new level; then the
Haiku H0-H3 core batches (30 tasks x 2 trials).

## Open problems

- Haiku 5.5 runs at the provider default (adaptive thinking, effort medium); LiteLLM returns no thinking blocks or
  reasoning-token counts, so thinking use cannot be seen in traces. Same setting for every level, so the comparison stays fair.
- Pilot rows 1-15 were priced with older cost code (no cache-write premium; OpenRouter at list price without cache
  discount, ~80% too high). Rows are append-only, so they stay; analysis must recompute cost from traces with
  runner.build_row, and exclude pilot batches (20261121T180557, 20261121T184215, 20261121T184407) from study results.
- Gate coverage gap: the insurance-covered-reason rule (health/weather) is free text; the cancel gate allows any
  insured reservation and leaves the reason to the prompt. Report in the paper.
- The simulated customer sometimes goes off-script (task 7, H0-H2). Audit failed conversations for customer error
  before attributing failures to the agent (sample by hand; report the rate).
- Credit-error detection (`error=credit`) is a text match on the saved simulation; confirm it on a real credit error.
- H1-H3 agents must be registered with tau2's registry at runtime from our code (never by editing tau2-bench).
