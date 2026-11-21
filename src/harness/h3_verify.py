"""H3: confirm before write (deterministic) and verify after write (a separate verifier call).

Confirm: the policy requires an explicit "yes" after the action details are listed, before booking, changing flights,
cabin, bags or passengers, or cancelling. The gate checks that the customer's latest message is an explicit yes.
Verify: after a write succeeds, a separate verifier (same model as the agent, its own prompt, no tools) compares the
database change with what the customer agreed and with the rules. Its verdict is added to the tool result the agent sees.
"""

from __future__ import annotations

import json
import re

from tau2.data_model.message import SystemMessage, UserMessage
from tau2.utils.llm_utils import generate

CONFIRM_TOOLS = {"book_reservation", "cancel_reservation", "update_reservation_flights",
                 "update_reservation_baggages", "update_reservation_passengers"}

_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm(ed)?|go ahead|proceed|please do|do it|sounds good|"
                  r"that works|correct)\b", re.IGNORECASE)

CONFIRM_REASON = ("Not executed: before this change, list the exact action details to the customer and wait for an "
                  "explicit 'yes'. The customer's latest message is not a clear confirmation.")

VERIFIER_PROMPT = """You are a verifier for an airline customer-service agent. You do not talk to the customer.
You get the recent conversation, one database change the agent just made, and the result the system returned.
Check two things: (1) the change matches exactly what the customer asked for and confirmed; (2) it follows the rules below.
Answer on one line: "OK" if both hold, otherwise "PROBLEM: <what is wrong, in one sentence>".

<rules>
{rules}
</rules>"""


def latest_user_text(conversation: list) -> str:
    for m in reversed(conversation):
        if isinstance(m, UserMessage) and m.content:
            return m.content
    return ""


def is_confirmed(conversation: list) -> bool:
    return bool(_YES.search(latest_user_text(conversation)))


def _recent_dialogue(conversation: list, turns: int = 8) -> str:
    lines = [f"{m.role}: {m.content}" for m in conversation if m.role in ("user", "assistant") and m.content]
    return "\n".join(lines[-turns:])


def verify(llm: str, llm_args: dict, rules: str, conversation: list, tool_name: str, arguments: dict, result: str):
    """Returns (verdict text, the verifier's AssistantMessage for cost accounting)."""
    prompt = (f"Recent conversation:\n{_recent_dialogue(conversation)}\n\n"
              f"Database change made: {tool_name}({json.dumps(arguments, ensure_ascii=False)})\n"
              f"System result: {result[:2000]}")
    msg = generate(model=llm, tools=None,
                   messages=[SystemMessage(role="system", content=VERIFIER_PROMPT.format(rules=rules)),
                             UserMessage(role="user", content=prompt)],
                   call_name="h3_verifier", **llm_args)
    return (msg.content or "").strip().splitlines()[0] if msg.content else "OK", msg
