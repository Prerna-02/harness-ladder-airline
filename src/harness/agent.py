"""The harness agent: tau2's LLMAgent plus the H1-H3 layers, each behind a switch in levels.py.

H0 runs tau2's own `llm_agent` unchanged. H1-H3 run HarnessAgent, registered with tau2's registry at runtime
(tau2-bench's code is never edited). Everything the harness does inside a turn (blocked proposals, block reasons,
verifier verdicts, and the tokens those extra model calls used) is recorded in the returned message's raw_data and
usage, so the trace and the cost stay complete.
"""

from __future__ import annotations

from pathlib import Path

from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT, LLMAgent
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolMessage
from tau2.utils.llm_utils import generate

from src.harness import h2_gates, h3_verify
from src.harness.levels import LEVELS, Switches, tau2_agent_name

H1_RULES = (Path(__file__).parent / "h1_rules.md").read_text(encoding="utf-8")
MAX_BLOCKED_TRIES = 4  # after this many blocked proposals in one turn, the harness answers for the agent

CHECKLIST = """
Before you ask the user to confirm any database change, restate this checklist in your message, filled in:
- Action: the tool you will call and its exact arguments in plain words.
- Identity: user id looked up (B1); reservation read and belongs to the user (B2), if any.
- Rules: each rule number that applies and why it is met.
- Money and bags: price difference, refund, paid bags or certificate amount, and the payment method, if any.
Then ask for an explicit "yes"."""

FALLBACK_TEXT = "I'm sorry, but I can't complete that request because it is not allowed under our policy."


def _merge_usage(final: AssistantMessage, extra: list[AssistantMessage]) -> None:
    """Add the tokens and billed cost of internal model calls into the message that leaves this turn."""
    if not extra:
        return
    usage = dict(final.usage or {})
    raw = dict(final.raw_data or {})
    raw_usage = dict(raw.get("usage") or {})
    details = dict(raw_usage.get("prompt_tokens_details") or {})
    for m in extra:
        for k in ("prompt_tokens", "completion_tokens"):
            usage[k] = (usage.get(k) or 0) + ((m.usage or {}).get(k) or 0)
        ru = (m.raw_data or {}).get("usage") or {}
        for k in ("cost", "cache_read_input_tokens", "cache_creation_input_tokens"):
            if isinstance(ru.get(k), (int, float)):
                raw_usage[k] = (raw_usage.get(k) or 0) + ru[k]
        cached = (ru.get("prompt_tokens_details") or {}).get("cached_tokens")
        if isinstance(cached, int):
            details["cached_tokens"] = (details.get("cached_tokens") or 0) + cached
    if details:
        raw_usage["prompt_tokens_details"] = details
    raw["usage"] = raw_usage
    final.usage, final.raw_data = usage, raw


class HarnessAgent(LLMAgent):
    def __init__(self, tools, domain_policy, llm, llm_args, switches: Switches, level: str):
        rules = H1_RULES if switches.structured_rules else domain_policy
        super().__init__(tools=tools, domain_policy=rules, llm=llm, llm_args=llm_args)
        self.switches, self.level = switches, level
        # Live airline database, read-only: gates inspect it, never change it.
        self.db = tools[0]._func.__self__.db

    @property
    def system_prompt(self) -> str:
        instruction = AGENT_INSTRUCTION + (CHECKLIST if self.switches.task_checklist else "")
        return SYSTEM_PROMPT.format(domain_policy=self.domain_policy, agent_instruction=instruction)

    # ---------- screening a proposed message ----------

    def _screen(self, msg: AssistantMessage, history: list) -> list[tuple[str, str]]:
        """(gate, reason) for each blocked tool call in msg; empty if everything may run."""
        blocks = []
        for tc in msg.tool_calls or []:
            if self.switches.confirm_before_write and tc.name in h3_verify.CONFIRM_TOOLS and not h3_verify.is_confirmed(history):
                blocks.append(("h3_confirm_before_write", h3_verify.CONFIRM_REASON))
                continue
            if self.switches.code_gates:
                decision, gate = h2_gates.check(tc, self.db, history)
                if isinstance(decision, h2_gates.Block):
                    blocks.append((gate, decision.reason))
        return blocks

    def _rejections(self, msg: AssistantMessage, history: list) -> list[ToolMessage]:
        out = []
        for tc in msg.tool_calls or []:
            blocked = [r for r in self._screen(AssistantMessage(role="assistant", tool_calls=[tc]), history)]
            if blocked:
                text = f"BLOCKED by policy check: {blocked[0][1]}" if self.switches.gate_reason else "BLOCKED."
            else:
                text = "Not executed: another tool call in the same message was blocked. Make one tool call at a time."
            out.append(ToolMessage(id=tc.id, role="tool", content=text, requestor="assistant", error=True,
                                   execution_status="rejected"))
        return out

    # ---------- verifying a write that just ran ----------

    def _verify_results(self, message, state, extra: list, events: list):
        """Return the incoming message, with verifier verdicts added to results of successful writes (H3)."""
        results = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        last_calls = {tc.id: tc for m in state.messages if m.role == "assistant" for tc in (m.tool_calls or [])}
        checked = []
        for tm in results:
            tc = last_calls.get(tm.id)
            if tc is None or tc.name not in h2_gates.WRITE_TOOLS or tm.error:
                checked.append(tm)
                continue
            verdict, vmsg = h3_verify.verify(self.llm, self.llm_args, self.domain_policy, state.messages,
                                             tc.name, tc.arguments, tm.content or "")
            extra.append(vmsg)
            events.append({"type": "verify", "tool": tc.name, "verdict": verdict})
            checked.append(tm.model_copy(update={"content": f"{tm.content}\n\n[Verifier] {verdict}"}))
        if isinstance(message, MultiToolMessage):
            return message.model_copy(update={"tool_messages": checked})
        return checked[0]

    # ---------- one agent turn ----------

    def generate_next_message(self, message, state):
        extra: list[AssistantMessage] = []
        events: list[dict] = []
        if self.switches.verify_after_write and isinstance(message, (ToolMessage, MultiToolMessage)):
            message = self._verify_results(message, state, extra, events)
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        for _ in range(MAX_BLOCKED_TRIES):
            msg = generate(model=self.llm, tools=self.tools, messages=state.system_messages + state.messages,
                           call_name="agent_response", **self.llm_args)
            blocks = self._screen(msg, state.messages)
            if not blocks:
                break
            events += [{"type": "block", "gate": g, "reason": r,
                        "calls": [{"name": tc.name, "arguments": tc.arguments} for tc in msg.tool_calls]} for g, r in blocks]
            extra.append(msg)
            state.messages.append(msg)
            state.messages.extend(self._rejections(msg, state.messages[:-1]))
        else:  # every try was blocked (each blocked proposal is already in `extra`)
            events.append({"type": "fallback", "text": FALLBACK_TEXT})
            msg = AssistantMessage(role="assistant", content=FALLBACK_TEXT, usage={}, raw_data={})

        _merge_usage(msg, extra)
        if events:
            msg.raw_data = {**(msg.raw_data or {}), "harness": {"level": self.level, "events": events}}
        state.messages.append(msg)
        return msg, state


def _factory(level: str):
    def create(tools, domain_policy, **kwargs):
        return HarnessAgent(tools, domain_policy, kwargs.get("llm"), kwargs.get("llm_args") or {}, LEVELS[level], level)
    return create


def register_levels() -> None:
    """Register one tau2 agent per non-H0 level (idempotent within a process)."""
    from tau2.registry import registry
    for level in LEVELS:
        name = tau2_agent_name(level)
        if name != "llm_agent" and name not in registry._agent_factories:
            registry.register_agent_factory(_factory(level), name)
