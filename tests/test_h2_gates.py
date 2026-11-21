"""H2 gates: one allowed and one blocked case each, on the real airline database, plus an over-blocking check:
every expected write in the 30 core tasks must pass all gates (golden rule 4: core tasks only)."""

import json
from pathlib import Path

import pytest
import yaml
from tau2.data_model.message import AssistantMessage
from tau2.data_model.message import ToolCall as TC
from tau2.domains.airline.data_model import FlightDB
from tau2.domains.airline.tools import AirlineTools
from tau2.domains.airline.utils import AIRLINE_DB_PATH

from src.harness import h2_gates as g

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def db0():
    return FlightDB.load(AIRLINE_DB_PATH)


@pytest.fixture
def db(db0):
    return db0.model_copy(deep=True)


def call(name, **args):
    return TC(name=name, arguments=args)


def conv(*calls):
    """An agent history that made these (name, args) tool calls."""
    return [AssistantMessage(role="assistant", tool_calls=[TC(name=n, arguments=a)]) for n, a in calls]


def read(res):
    return conv(("get_user_details", {"user_id": res.user_id}), ("get_reservation_details", {"reservation_id": res.reservation_id}))


def find(db, pred):
    return next(r for r in db.reservations.values() if r.status is None and pred(r))


def flown(db, r):
    return any(g._flown(db, f.flight_number, f.date) for f in r.flights)


def flights_of(r):
    return [{"flight_number": f.flight_number, "date": f.date} for f in r.flights]


# ---------- one allowed + one blocked case per gate ----------

def test_auth_read_before_write(db):
    r = find(db, lambda r: True)
    c = call("cancel_reservation", reservation_id=r.reservation_id)
    assert g.auth_read_before_write(c, db, read(r)) == g.ALLOW
    assert isinstance(g.auth_read_before_write(c, db, conv(("get_user_details", {"user_id": r.user_id}))), g.Block)


def test_basic_economy_no_modify(db):
    r = find(db, lambda r: g._value(r.cabin) == "basic_economy")
    same = call("update_reservation_flights", reservation_id=r.reservation_id, cabin="economy", flights=flights_of(r), payment_id="x")
    assert g.basic_economy_no_modify(same, db, []) == g.ALLOW  # cabin change on the same flights is allowed
    other = call("update_reservation_flights", reservation_id=r.reservation_id, cabin="basic_economy",
                 flights=[{"flight_number": "HAT001", "date": "2024-05-20"}], payment_id="x")
    assert isinstance(g.basic_economy_no_modify(other, db, []), g.Block)


def test_modify_same_route(db):
    r = find(db, lambda r: g._value(r.flight_type) == "one_way" and len(r.flights) == 1)
    ok = call("update_reservation_flights", reservation_id=r.reservation_id, cabin=r.cabin, flights=flights_of(r), payment_id="x")
    assert g.modify_same_route(ok, db, []) == g.ALLOW
    wrong = next(fn for fn, f in db.flights.items() if f.destination != r.destination)
    bad = call("update_reservation_flights", reservation_id=r.reservation_id, cabin=r.cabin,
               flights=[{"flight_number": wrong, "date": r.flights[0].date}], payment_id="x")
    assert isinstance(g.modify_same_route(bad, db, []), g.Block)


def test_cabin_change_rules(db):
    fresh = find(db, lambda r: not flown(db, r) and g._value(r.cabin) == "economy")
    up = call("update_reservation_flights", reservation_id=fresh.reservation_id, cabin="business", flights=flights_of(fresh), payment_id="x")
    assert g.cabin_change_rules(up, db, []) == g.ALLOW
    old = find(db, lambda r: flown(db, r) and g._value(r.cabin) == "economy")
    up_old = call("update_reservation_flights", reservation_id=old.reservation_id, cabin="business", flights=flights_of(old), payment_id="x")
    assert isinstance(g.cabin_change_rules(up_old, db, []), g.Block)


def test_cancel_eligibility(db):
    biz = find(db, lambda r: g._value(r.cabin) == "business" and not flown(db, r))
    assert g.cancel_eligibility(call("cancel_reservation", reservation_id=biz.reservation_id), db, []) == g.ALLOW
    plain = find(db, lambda r: g._value(r.cabin) == "economy" and g._value(r.insurance) == "no" and not flown(db, r)
                 and r.created_at < "2024-05-14T15:00:00"
                 and all(g._status(db, f.flight_number, f.date) != "cancelled" for f in r.flights))
    assert isinstance(g.cancel_eligibility(call("cancel_reservation", reservation_id=plain.reservation_id), db, []), g.Block)


def test_passenger_count_fixed(db):
    r = find(db, lambda r: True)
    same = [p.model_dump() for p in r.passengers]
    assert g.passenger_count_fixed(call("update_reservation_passengers", reservation_id=r.reservation_id, passengers=same), db, []) == g.ALLOW
    more = same + [{"first_name": "A", "last_name": "B", "dob": "1990-01-01"}]
    assert isinstance(g.passenger_count_fixed(call("update_reservation_passengers", reservation_id=r.reservation_id, passengers=more), db, []), g.Block)


def test_bags_add_only_and_price(db):
    r = find(db, lambda r: g._value(r.cabin) == "economy")
    free = g.FREE_BAGS[g._value(db.users[r.user_id].membership)]["economy"] * len(r.passengers)
    total = max(r.total_baggages, free) + 1
    ok = call("update_reservation_baggages", reservation_id=r.reservation_id, total_baggages=total, nonfree_baggages=total - free, payment_id="x")
    assert g.bags_add_only_and_price(ok, db, []) == g.ALLOW
    wrong_price = call("update_reservation_baggages", reservation_id=r.reservation_id, total_baggages=total, nonfree_baggages=total, payment_id="x")
    assert isinstance(g.bags_add_only_and_price(wrong_price, db, []), g.Block)
    r2 = find(db, lambda r: r.total_baggages > 0)
    remove = call("update_reservation_baggages", reservation_id=r2.reservation_id, total_baggages=r2.total_baggages - 1, nonfree_baggages=0, payment_id="x")
    assert isinstance(g.bags_add_only_and_price(remove, db, []), g.Block)


def test_payment_rules(db):
    r = find(db, lambda r: any(m.source == "credit_card" for m in db.users[r.user_id].payment_methods.values()))
    card = next(i for i, m in db.users[r.user_id].payment_methods.items() if m.source == "credit_card")
    ok = call("update_reservation_flights", reservation_id=r.reservation_id, cabin=r.cabin, flights=flights_of(r), payment_id=card)
    assert g.payment_rules(ok, db, []) == g.ALLOW
    stranger = call("update_reservation_flights", reservation_id=r.reservation_id, cabin=r.cabin, flights=flights_of(r), payment_id="credit_card_0000000")
    assert isinstance(g.payment_rules(stranger, db, []), g.Block)


def test_booking_limits(db):
    p = {"first_name": "A", "last_name": "B", "dob": "1990-01-01"}
    assert g.booking_limits(call("book_reservation", passengers=[p] * 5), db, []) == g.ALLOW
    assert isinstance(g.booking_limits(call("book_reservation", passengers=[p] * 6), db, []), g.Block)


def test_compensation_eligibility_and_amount(db):
    def has(status):
        return lambda r: any(g._status(db, f.flight_number, f.date) == status for f in r.flights)
    r = find(db, lambda r: has("cancelled")(r) and g._value(db.users[r.user_id].membership) in ("silver", "gold"))
    right = call("send_certificate", user_id=r.user_id, amount=100 * len(r.passengers))
    assert g.compensation_eligibility_and_amount(right, db, []) == g.ALLOW
    too_much = call("send_certificate", user_id=r.user_id, amount=100 * len(r.passengers) + 50)
    assert isinstance(g.compensation_eligibility_and_amount(too_much, db, []), g.Block)


def test_read_tools_are_never_gated(db):
    assert g.check(call("get_user_details", user_id="nobody"), db, []) == (g.ALLOW, "")


# ---------- over-blocking: expected core writes must all pass ----------

def _core_tasks():
    core = yaml.safe_load((ROOT / "configs" / "tasks.yaml").read_text(encoding="utf-8"))["core"]
    tasks = json.loads((ROOT / "tau2-bench/data/tau2/domains/airline/tasks.json").read_text(encoding="utf-8"))
    return [t for t in tasks if t["id"] in core]  # held-out tasks are filtered out here and never inspected


@pytest.mark.parametrize("task", _core_tasks(), ids=lambda t: t["id"])
def test_expected_core_writes_pass_every_gate(db, task):
    tools = AirlineTools(db)
    history = []
    for a in task["evaluation_criteria"]["actions"]:
        c = TC(name=a["name"], arguments=a["arguments"])
        # The answer key lists only key actions; a correct agent also looks up the user and reservation first.
        res = db.reservations.get(a["arguments"].get("reservation_id", ""))
        uid = res.user_id if res else a["arguments"].get("user_id")
        history += conv(("get_user_details", {"user_id": uid}),
                        *([("get_reservation_details", {"reservation_id": res.reservation_id})] if res else []))
        decision, gate = g.check(c, db, history)
        assert decision == g.ALLOW, f"task {task['id']}: {gate} blocked expected {a['name']}: {decision}"
        getattr(tools, a["name"])(**a["arguments"])  # apply it so later actions see the updated database
        history.append(AssistantMessage(role="assistant", tool_calls=[c]))
