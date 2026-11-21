"""HarnessAgent turn logic with a scripted fake model (no API calls)."""

import pytest
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.domains.airline.environment import get_environment

from src.harness import agent as A
from src.harness.levels import LEVELS, tau2_agent_name


def reply(*, text=None, call=None, tokens=(100, 10), cost=None):
    usage = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}
    raw = {"usage": {**usage, **({"cost": cost} if cost is not None else {})}}
    calls = [ToolCall(id=f"c{id(call)}", name=call[0], arguments=call[1])] if call else None
    return AssistantMessage(role="assistant", content=text, tool_calls=calls, usage=usage, raw_data=raw)


@pytest.fixture
def make(monkeypatch):
    def _make(level, script):
        env = get_environment()
        ag = A.HarnessAgent(env.get_tools(), env.policy, "fake-model", {}, LEVELS[level], level)
        it = iter(script)
        monkeypatch.setattr(A, "generate", lambda **kw: next(it))
        monkeypatch.setattr(A.h3_verify, "generate", lambda **kw: next(it))  # the verifier shares the script
        return ag, ag.get_init_state()
    return _make


def plain_economy_reservation(db):
    """An economy, uninsured, old reservation with nothing flown: the policy forbids cancelling it."""
    from src.harness import h2_gates as g
    return next(r for r in db.reservations.values() if r.status is None and g._value(r.cabin) == "economy"
                and g._value(r.insurance) == "no" and r.created_at < "2024-05-14"
                and not any(g._flown(db, f.flight_number, f.date) or g._status(db, f.flight_number, f.date) == "cancelled"
                            for f in r.flights))


def test_h2_blocks_then_lets_the_retry_through(make):
    ag, st = make("H2", [])
    r = plain_economy_reservation(ag.db)
    bad = reply(call=("cancel_reservation", {"reservation_id": r.reservation_id}), cost=0.001)
    good = reply(text="I'm sorry, this reservation can't be cancelled under our policy.", cost=0.002)
    ag, st = make("H2", [bad, good])
    st.messages += [reply(call=("get_user_details", {"user_id": r.user_id})),
                    reply(call=("get_reservation_details", {"reservation_id": r.reservation_id}))]
    msg, st = ag.generate_next_message(UserMessage(role="user", content="Cancel it please, yes."), st)
    assert msg.content.startswith("I'm sorry")
    events = msg.raw_data["harness"]["events"]
    assert [e["gate"] for e in events] == ["cancel_eligibility"]
    assert msg.usage == {"prompt_tokens": 200, "completion_tokens": 20}  # blocked call's tokens are counted
    assert msg.raw_data["usage"]["cost"] == pytest.approx(0.003)
    assert any(getattr(m, "execution_status", None) == "rejected" and "BLOCKED by policy check" in m.content
               for m in st.messages)
    assert ag.db.reservations[r.reservation_id].status is None  # the gate never touched the database


def test_h3_needs_an_explicit_yes(make):
    ag, st = make("H3", [])
    r = next(iter(ag.db.reservations.values()))
    want = reply(call=("update_reservation_passengers", {"reservation_id": r.reservation_id,
                                                         "passengers": [p.model_dump() for p in r.passengers]}))
    ag, st = make("H3", [want, reply(text="Shall I go ahead? Please reply yes.")])
    st.messages += [reply(call=("get_user_details", {"user_id": r.user_id})),
                    reply(call=("get_reservation_details", {"reservation_id": r.reservation_id}))]
    msg, st = ag.generate_next_message(UserMessage(role="user", content="Change the name on my booking."), st)
    assert msg.raw_data["harness"]["events"][0]["gate"] == "h3_confirm_before_write"
    ag2, st2 = make("H3", [want])
    st2.messages += st.messages[:2]
    msg2, _ = ag2.generate_next_message(UserMessage(role="user", content="Yes, go ahead."), st2)
    assert msg2.tool_calls and "harness" not in msg2.raw_data


def test_h3_verifier_reads_each_successful_write(make):
    from tau2.data_model.message import ToolMessage
    verdict = reply(text="PROBLEM: the customer asked for 2 bags, not 3.", tokens=(50, 5), cost=0.0005)
    answer = reply(text="Sorry, let me fix that.", tokens=(100, 10), cost=0.001)
    ag, st = make("H3", [verdict, answer])
    write = reply(call=("update_reservation_baggages", {"reservation_id": "X", "total_baggages": 3}))
    st.messages.append(write)
    result = ToolMessage(id=write.tool_calls[0].id, role="tool", content='{"total_baggages": 3}', requestor="assistant")
    msg, st = ag.generate_next_message(result, st)
    assert "[Verifier] PROBLEM" in st.messages[-2].content  # the agent saw the verdict with the tool result
    assert result.content == '{"total_baggages": 3}'          # the trace's own tool result is untouched
    assert msg.usage == {"prompt_tokens": 150, "completion_tokens": 15}
    assert msg.raw_data["harness"]["events"][0]["type"] == "verify"


def test_fallback_after_repeated_blocks(make):
    ag, st = make("H2", [])
    r = plain_economy_reservation(ag.db)
    bad = reply(call=("cancel_reservation", {"reservation_id": r.reservation_id}))
    ag, st = make("H2", [bad] * A.MAX_BLOCKED_TRIES)
    msg, _ = ag.generate_next_message(UserMessage(role="user", content="yes cancel"), st)
    assert msg.content == A.FALLBACK_TEXT and not msg.tool_calls
    assert msg.usage["prompt_tokens"] == 100 * A.MAX_BLOCKED_TRIES


def test_h1_uses_structured_rules_and_checklist(make):
    ag, _ = make("H1", [])
    assert "Airline agent rules (structured)" in ag.system_prompt and "restate this checklist" in ag.system_prompt
    h0_like, _ = make("H2", [])
    assert h0_like.system_prompt == ag.system_prompt  # H2 adds gates, not prompt text: one variable at a time


def test_registered_names():
    A.register_levels()
    from tau2.registry import registry
    for lvl in ("H1", "H2", "H3"):
        assert tau2_agent_name(lvl) in registry._agent_factories
