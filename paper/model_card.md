# Model card: harnessed customer-service agents on τ²-bench airline

*How much harness does a low-cost AI agent need?* This card describes the agent systems tested in the study
(model + harness level), how they were evaluated, the results, and their limits. Last updated 2026-11-24.
Code, traces and data: <https://github.com/Prerna-02/harness-ladder-airline>.

## 1. Overview

| | |
|---|---|
| **What is evaluated** | A tool-using customer-service agent for an airline (look up users and bookings; book, change, cancel, add bags, issue compensation), built from one language model plus one of four harness levels. |
| **Question** | With the model and tasks held fixed, how do task success, rule-breaking and cost change as harness layers are added one at a time? |
| **Author** | Prerna Gade, Master of AI in Business. |
| **Status** | Research prototype. Core study complete (13 conditions); extension runs pending. |
| **Not for** | Production use with real customers or real payments. The agents act on a simulated database only. |

## 2. Systems evaluated

### Models

| Model | API ID (via LiteLLM) | Provider / host | Settings | Price per 1M tokens (in / cached in / out) |
|---|---|---|---|---|
| Claude Haiku 5.5 | `anthropic/claude-haiku-5-5` | Anthropic API | provider default (no temperature sent; Claude 5.5 rejects non-default values); prompt caching on the system prompt | $0.10 / $0.01 / $0.50 |
| DeepSeek V4.1 Flash | `openrouter/deepseek/deepseek-v4.1-flash` | OpenRouter, pinned to `baidu/fp8`, no fallback | temperature 0 | $0.05 / $0.001 / $0.199 |
| Qwen3.6-35B-A3B | `openrouter/qwen/qwen3.6-35b-a3b` | OpenRouter, pinned to `deepinfra/fp8`, no fallback | temperature 0 | $0.10 / $0.10 / $0.95 |
| Claude Sonnet 5.5 (reference) | `anthropic/claude-sonnet-5-5` | Anthropic API | as Haiku | $2.00 / $0.10 / $10.00 |

Every agent call has `max_tokens` 8192 and a 180-second timeout. Prices are as configured on 2026-11-21/22
(`configs/models.yaml`). OpenRouter cost is the billed `usage.cost` per response; Anthropic cost is computed from token
counts, with cache writes at 1.25× and cache reads at 0.1× input.

### Harness levels (cumulative; `src/harness/levels.py`)

| Level | Adds | Where |
|---|---|---|
| **H0** | τ²-bench's standard `llm_agent` with the airline policy text. | tau2 |
| **H1** | The policy rewritten as short numbered rules (A1–F), plus a checklist the agent restates in its confirmation message. | `src/harness/h1_rules.md`, `agent.py` |
| **H2** | 10 deterministic, read-only checks on every write tool call. A blocked call is not executed; the agent gets the reason and may retry (up to 4 times). Checks: authenticated read before write, no changes to basic economy flights, same route, cabin-change rules, cancellation eligibility, fixed passenger count, bags add-only with correct paid count, payment rules, booking limits, compensation eligibility and amount. | `src/harness/h2_gates.py` |
| **H3** | No write without an explicit “yes” in the customer's latest message, plus a verifier call (same model) that reviews each executed write. | `src/harness/h3_verify.py` |

## 3. Evaluation setup

- **Benchmark:** τ²-bench (sierra-research/tau2-bench) at commit `768f3d8`, airline domain, unmodified.
- **Tasks:** the published train split as the 30 core tasks; the 20 test tasks are held out and were never looked at
  while writing the H2 checks. Sonnet ran the fixed half `core_15` (15 tasks drawn with seed 300 before any Sonnet
  study run) because of credit limits.
- **Trials:** 2 per task per condition (60 conversations; 30 for Sonnet).
- **Simulated customer:** `openrouter/mistralai/mistral-small-2603`, served by Mistral, temperature 0, `max_tokens`
  1024. Identical for every run.
- **Grader:** τ²-bench reward = database end state matches the answer key × the agent told the customer the required
  facts. The “told the customer” check uses τ²-bench's deterministic substring scorer (case-insensitive, commas
  removed), not its LLM judge.
- **Exclusions:** pilot batches; conversations cut off by a credit error, a customer reply hitting its length cap
  (`customer_loop`), or a host error (`infrastructure_error`). One Qwen H3 conversation was excluded this way.
  749 conversations are scored.

### Metrics

| Metric | Definition |
|---|---|
| Pass rate | Share of scored conversations with reward 1, with a 95% Wilson interval. |
| Pass^2 | Share of tasks passed on both trials. |
| Rule violation | An executed write that breaks a policy rule, found by replaying every write through the H2 checks on a fresh copy of the database (independent of the level that ran). |
| Safe completion | A passed conversation with no rule violation. |
| Cost per safe completion | Agent spend divided by safe completions (customer cost excluded; it is the same for every condition). |
| Over-block | A harness block of a call that the task's answer key expects. |

## 4. Results

### All 30 core tasks (60 conversations per condition)

| Model | Level | Passed | Pass rate (95% range) | Pass^2 | Rule violations | Blocks | Cost / conversation | Cost / safe completion |
|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | H0 | 45/60 | 75% (63–84) | 0.67 | 1 | 0 | $0.0059 | $0.0078 |
| Haiku 5.5 | H1 | 45/60 | 75% (63–84) | 0.70 | 0 | 0 | $0.0053 | $0.0071 |
| Haiku 5.5 | H2 | 45/60 | 75% (63–84) | 0.63 | 0 | 1 | $0.0061 | $0.0080 |
| Haiku 5.5 | H3 | 45/60 | 75% (63–84) | 0.63 | 0 | 0 | $0.0064 | $0.0085 |
| DeepSeek V4.1 Flash | H0 | 41/60 | 68% (56–79) | 0.60 | 6 | 0 | $0.0012 | $0.0018 |
| DeepSeek V4.1 Flash | H1 | 45/60 | 75% (63–84) | 0.63 | 0 | 0 | $0.0015 | $0.0020 |
| DeepSeek V4.1 Flash | H2 | **49/60** | **82% (70–89)** | 0.73 | 0 | 1 | $0.0016 | $0.0019 |
| DeepSeek V4.1 Flash | H3 | 47/60 | 78% (66–87) | 0.70 | 0 | 1 | $0.0019 | $0.0023 |
| Qwen3.6-35B-A3B | H0 | 46/60 | 77% (65–86) | 0.70 | 0 | 0 | $0.021 | $0.027 |
| Qwen3.6-35B-A3B | H1 | 46/60 | 77% (65–86) | 0.67 | 0 | 0 | $0.019 | $0.025 |
| Qwen3.6-35B-A3B | H2 | 44/60 | 73% (61–83) | 0.70 | 0 | 0 | $0.018 | $0.025 |
| Qwen3.6-35B-A3B | H3 | 43/59 | 73% (60–83) | 0.63 | 0 | 0 | $0.019 | $0.027 |

### Same 15 tasks (`core_15`, 30 conversations per condition), for the Sonnet comparison

| Condition | Passed | Pass rate (95% range) | Cost / conversation |
|---|---|---|---|
| Haiku 5.5 · H1 | 26/30 | 87% (70–95) | $0.0056 |
| Haiku 5.5 · H3 | 25/30 | 83% (66–93) | $0.0069 |
| DeepSeek V4.1 Flash · H2 | 24/30 | 80% (63–90) | $0.0017 |
| Qwen3.6-35B-A3B · H1 | 24/30 | 80% (63–90) | $0.020 |
| Haiku 5.5 · H0 / H2 | 22/30 | 73% (56–86) | $0.0066 / $0.0070 |
| Qwen3.6-35B-A3B · H2 | 22/30 | 73% (56–86) | $0.019 |
| DeepSeek V4.1 Flash · H1 / H3 | 21/30 | 70% (52–83) | $0.0016 / $0.0021 |
| Qwen3.6-35B-A3B · H3 | 21/30 | 70% (52–83) | $0.021 |
| Qwen3.6-35B-A3B · H0 | 20/30 | 67% (49–81) | $0.024 |
| DeepSeek V4.1 Flash · H0 | 19/30 | 63% (46–78) | $0.0012 |
| **Sonnet 5.5 · H0** | **19/30** | **63% (46–78)** | **$0.112** |

### Main findings

1. **The harness helped only the model that broke rules.** DeepSeek went from 6 rule violations at H0 to 0 at every
   higher level, and its pass rate rose from 68% to 82% at H2. Clearer written rules (H1) alone removed its
   violations. Haiku (1 violation at H0) and Qwen (none) stayed flat; their differences are within noise.
2. **Paying for a stronger model did not buy a better score here.** On the same 15 tasks, Sonnet 5.5 with no harness
   scored 63%, tied with DeepSeek H0 and below DeepSeek H2 (80%) and Haiku H1 (87%), at 20–65× the cost per
   conversation. With 30 conversations the ranges overlap: the evidence supports “not better”, not “worse”.
3. **The harness is cheap.** H2 and H3 added at most about $0.0007 per conversation for DeepSeek. The code checks
   fired 3 times in 480 harness conversations; 1 of those was an over-block.
4. **Harnesses fix rule-breaking, not forgetting.** Of 189 failures, 6 broke a rule; 76 missed a needed change,
   40 made a wrong or unrequested change, 18 did not tell the customer a required fact, and 49 had the right kind of
   change with a wrong detail.

## 5. Audits

- **Over-block (DeepSeek H3, task 17).** The agent sent three dependent changes in one message; each was checked
  against the database as it was before any of them ran, so the bag count looked wrong before the cabin upgrade.
  The agent retried one call at a time and all passed. Kept unchanged so H2/H3 stay identical across runs.
- **Grader format check.** In all 18 conversations that made every right change but failed the “told the customer”
  check, the fact did not appear in another format (number words, decimals). All 18 are real omissions.
- **Customer error (hand audit).** 26 failed conversations, 2 per condition (pandas `random_state=300`), read by eye:
  16 agent errors, 7 customer errors (27%, 95% range 14–46%), 3 unclear (`results/audit_failures.csv`). Customer
  errors include invented booking IDs, refusing the payment its script prefers, accepting a price over its budget,
  and asking to cancel bookings its script never mentions (task 7: 9 of 26 conversations). They affect every condition,
  so they lower absolute pass rates more than they bias comparisons.

## 6. Limitations

- **Sample size:** 60 conversations per condition (30 for Sonnet); 95% ranges are about ±11 points (±16 for 30).
- **One domain and one simulated customer:** airline support only; results may not carry to other domains or real
  people. The simulated customer sometimes goes off-script.
- **Rules by construction:** violations at H2/H3 are counted with the same rules H2 enforces, so their zeros are partly
  built in. The insurance-reason rule is free text and is not checked in code.
- **Tasks rarely tempt rule-breaking,** which limits how much a harness can show here.
- **H3 verifier** judges each change against the whole request, so it sometimes flags a problem mid-way through a
  multi-step task (advisory only).
- **Qwen empty replies:** Qwen sometimes returned an empty message; τ²-bench restarts the conversation (up to 3 tries).
  The billed cost of failed attempts is not recorded.
- **Point in time:** model versions, hosts and prices as of November 2026.
- **Haiku/Sonnet thinking:** run at the provider default; thinking tokens are not visible in traces.

## 7. Ethical considerations and intended use

- Use these results to decide **how to test and guard** an agent, not as a ranking of vendors.
- The agents can move simulated money (refunds, certificates). A real deployment would need human approval for
  payments, logging, and testing on its own policies and customers.
- No real customer data is used; τ²-bench's database is synthetic.

## 8. Reproducing

```bash
# from the repo root, inside tau2-bench's environment (Python 3.12); on Windows set PYTHONUTF8=1
uv run --project tau2-bench python -m pytest tests/
uv run --project tau2-bench python -m src.runner --model deepseek-v4.1-flash --level H2 --tasks core --trials 2 --dry-run
uv run --project tau2-bench python -m src.runner --model deepseek-v4.1-flash --level H2 --tasks core --trials 2
uv run --project tau2-bench python -m src.analyze
uv run --project tau2-bench python -m src.export_dashboard
```

Every conversation is one row in `results/runs.csv` (append-only) with its full trace in `results/traces/`.
Seed 300; τ²-bench commit `768f3d8`; API keys only in `.env` (never committed).
