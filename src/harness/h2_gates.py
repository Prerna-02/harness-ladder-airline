"""H2: deterministic policy gates for state-changing airline tool calls.

Each gate is a pure, read-only function (call, db, conversation) -> Allow | Block(reason).
Written from the airline policy (tau2-bench data/tau2/domains/airline/policy.md) and the core tasks only (golden rule 4).

`call` is a tau2 ToolCall (name, arguments). `db` is the live FlightDB. `conversation` is the agent's message history
(tau2 messages) before the proposed call. Gates never modify db or conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

NOW = datetime(2024, 5, 15, 15, 0, 0)  # policy: "The current time is 2024-05-15 15:00:00 EST."

WRITE_TOOLS = {
    "book_reservation", "cancel_reservation", "update_reservation_flights",
    "update_reservation_baggages", "update_reservation_passengers", "send_certificate",
}
RESERVATION_WRITES = WRITE_TOOLS - {"book_reservation", "send_certificate"}

# policy "Checked bag allowance": free bags per passenger by membership and cabin
FREE_BAGS = {
    "regular": {"basic_economy": 0, "economy": 1, "business": 2},
    "silver": {"basic_economy": 1, "economy": 2, "business": 3},
    "gold": {"basic_economy": 2, "economy": 3, "business": 4},
}


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Block:
    reason: str


Decision = Allow | Block
ALLOW = Allow()


# ---------- helpers (read-only) ----------

def _prior_calls(conversation: list, name: str) -> list[dict]:
    """Arguments of every earlier tool call with this name, oldest first."""
    return [tc.arguments for m in conversation if getattr(m, "role", None) == "assistant"
            for tc in (getattr(m, "tool_calls", None) or []) if tc.name == name]


def _flown(db: Any, flight_number: str, date: str) -> bool:
    status = db.flights[flight_number].dates.get(date) if flight_number in db.flights else None
    return getattr(status, "status", None) in ("flying", "landed")


def _status(db: Any, flight_number: str, date: str) -> str | None:
    status = db.flights[flight_number].dates.get(date) if flight_number in db.flights else None
    return getattr(status, "status", None)


def _flight_key(f: Any) -> tuple[str, str]:
    return (f["flight_number"], f["date"]) if isinstance(f, dict) else (f.flight_number, f.date)


def _reservation(db: Any, args: dict):
    return db.reservations.get(args.get("reservation_id", ""))


def _value(x: Any) -> str:
    return getattr(x, "value", x)


# ---------- gates ----------

def auth_read_before_write(call, db, conversation) -> Decision:
    """Identify the user (get_user_details) and read the reservation before writing to it."""
    args = call.arguments
    read_users = {a.get("user_id") for a in _prior_calls(conversation, "get_user_details")}
    if call.name in ("book_reservation", "send_certificate"):
        if args.get("user_id") not in read_users:
            return Block(f"Look up the user first with get_user_details(user_id='{args.get('user_id')}') "
                         "before booking or sending a certificate.")
        return ALLOW
    if call.name in RESERVATION_WRITES:
        res = _reservation(db, args)
        if res is None:
            return Block(f"Reservation '{args.get('reservation_id')}' does not exist.")
        read_res = {a.get("reservation_id") for a in _prior_calls(conversation, "get_reservation_details")}
        if res.reservation_id not in read_res:
            return Block(f"Read the reservation first with get_reservation_details('{res.reservation_id}') "
                         "before changing or cancelling it.")
        if res.user_id not in read_users:
            return Block(f"Reservation {res.reservation_id} belongs to user '{res.user_id}'. Get the user id from "
                         "the customer and look it up with get_user_details before acting on this reservation.")
    return ALLOW


def basic_economy_no_modify(call, db, conversation) -> Decision:
    """Basic economy flights cannot be changed (a cabin change on the same flights is allowed)."""
    if call.name != "update_reservation_flights":
        return ALLOW
    res = _reservation(db, call.arguments)
    if res is None or _value(res.cabin) != "basic_economy":
        return ALLOW
    old = {(f.flight_number, f.date) for f in res.flights}
    new = {_flight_key(f) for f in call.arguments.get("flights", [])}
    if new != old:
        return Block("This reservation is basic economy: its flights cannot be modified. "
                     "Only the cabin can be changed, keeping the same flights.")
    return ALLOW


def modify_same_route(call, db, conversation) -> Decision:
    """A modified reservation keeps its origin, destination and trip type."""
    if call.name != "update_reservation_flights":
        return ALLOW
    res = _reservation(db, call.arguments)
    flights = call.arguments.get("flights", [])
    if res is None or not flights:
        return ALLOW
    legs = []
    for f in flights:
        fn, _ = _flight_key(f)
        if fn not in db.flights:
            return Block(f"Flight {fn} does not exist.")
        legs.append((db.flights[fn].origin, db.flights[fn].destination))
    start, end = legs[0][0], legs[-1][1]
    if _value(res.flight_type) == "one_way":
        ok = start == res.origin and end == res.destination
    else:
        ok = start == res.origin and end == res.origin and any(d == res.destination for _, d in legs)
    if not ok:
        return Block(f"The new flights must keep the reservation's origin ({res.origin}), destination "
                     f"({res.destination}) and trip type ({_value(res.flight_type)}).")
    return ALLOW


def cabin_change_rules(call, db, conversation) -> Decision:
    """No cabin change once any flight in the reservation has been flown."""
    if call.name != "update_reservation_flights":
        return ALLOW
    res = _reservation(db, call.arguments)
    if res is None or _value(call.arguments.get("cabin")) == _value(res.cabin):
        return ALLOW
    if any(_flown(db, f.flight_number, f.date) for f in res.flights):
        return Block("The cabin cannot be changed because a flight in this reservation has already been flown.")
    return ALLOW


def cancel_eligibility(call, db, conversation) -> Decision:
    """Cancel only if nothing is flown and one condition holds: booked <24h ago, airline-cancelled, business, insured.

    Insurance covers health or weather reasons only; the reason is in free text, so that part is left to the
    prompt (H1) and the gate allows any insured reservation.
    """
    if call.name != "cancel_reservation":
        return ALLOW
    res = _reservation(db, call.arguments)
    if res is None:
        return ALLOW
    if res.status == "cancelled":
        return Block("This reservation is already cancelled.")
    if any(_flown(db, f.flight_number, f.date) for f in res.flights):
        return Block("A flight in this reservation has already been flown, so it cannot be cancelled. "
                     "Transfer the customer to a human agent instead.")
    recent = NOW - datetime.fromisoformat(res.created_at) <= timedelta(hours=24)
    airline_cancelled = any(_status(db, f.flight_number, f.date) == "cancelled" for f in res.flights)
    if recent or airline_cancelled or _value(res.cabin) == "business" or _value(res.insurance) == "yes":
        return ALLOW
    return Block("This reservation cannot be cancelled: it was booked more than 24 hours ago, no flight was "
                 "cancelled by the airline, it is not business class, and it has no travel insurance.")


def passenger_count_fixed(call, db, conversation) -> Decision:
    if call.name != "update_reservation_passengers":
        return ALLOW
    res = _reservation(db, call.arguments)
    new = call.arguments.get("passengers", [])
    if res is not None and len(new) != len(res.passengers):
        return Block(f"The number of passengers cannot change (reservation has {len(res.passengers)}, "
                     f"request has {len(new)}).")
    return ALLOW


def _expected_nonfree(membership: str, cabin: str, passengers: int, total: int) -> int:
    return max(0, total - FREE_BAGS[membership][cabin] * passengers)


def bags_add_only_and_price(call, db, conversation) -> Decision:
    """Bags can be added, never removed; the non-free count must follow the free allowance."""
    args = call.arguments
    if call.name == "update_reservation_baggages":
        res = _reservation(db, args)
        if res is None:
            return ALLOW
        total = int(args.get("total_baggages", 0))
        if total < res.total_baggages:
            return Block(f"Checked bags can be added but not removed (currently {res.total_baggages}).")
        membership = _value(db.users[res.user_id].membership)
        want = _expected_nonfree(membership, _value(res.cabin), len(res.passengers), total)
    elif call.name == "book_reservation":
        user = db.users.get(args.get("user_id", ""))
        if user is None:
            return ALLOW
        want = _expected_nonfree(_value(user.membership), _value(args.get("cabin")),
                                 len(args.get("passengers", [])), int(args.get("total_baggages", 0)))
    else:
        return ALLOW
    if int(args.get("nonfree_baggages", -1)) != want:
        return Block(f"Wrong number of paid bags: with this membership, cabin and passenger count, "
                     f"{args.get('total_baggages')} total bags means {want} paid bags.")
    return ALLOW


def payment_rules(call, db, conversation) -> Decision:
    """Payment methods must be in the user's profile; booking limits per method type; flight changes paid by one
    gift card or credit card."""
    args = call.arguments
    if call.name == "book_reservation":
        user = db.users.get(args.get("user_id", ""))
        if user is None:
            return ALLOW
        ids = [p["payment_id"] if isinstance(p, dict) else p.payment_id for p in args.get("payment_methods", [])]
    elif call.name in ("update_reservation_flights", "update_reservation_baggages"):
        res = _reservation(db, args)
        if res is None:
            return ALLOW
        user = db.users[res.user_id]
        ids = [args.get("payment_id", "")]
    else:
        return ALLOW
    missing = [i for i in ids if i not in user.payment_methods]
    if missing:
        return Block(f"Payment method {missing[0]} is not in the user's profile.")
    kinds = [user.payment_methods[i].source for i in ids]
    if call.name == "book_reservation" and (kinds.count("certificate") > 1 or kinds.count("credit_card") > 1
                                            or kinds.count("gift_card") > 3):
        return Block("A booking can use at most one travel certificate, one credit card and three gift cards.")
    if call.name == "update_reservation_flights" and kinds[0] not in ("gift_card", "credit_card"):
        return Block("Flight changes must be paid or refunded with a single gift card or credit card.")
    return ALLOW


def booking_limits(call, db, conversation) -> Decision:
    if call.name == "book_reservation" and len(call.arguments.get("passengers", [])) > 5:
        return Block("A reservation can have at most five passengers.")
    return ALLOW


def compensation_eligibility_and_amount(call, db, conversation) -> Decision:
    """Certificates only for an eligible reservation with a cancelled ($100 x passengers) or delayed ($50 x
    passengers, after that reservation was changed or cancelled) flight. Eligible: silver/gold, insured, or business.
    Whether the customer asked first is left to the prompt (H1)."""
    if call.name != "send_certificate":
        return ALLOW
    user = db.users.get(call.arguments.get("user_id", ""))
    if user is None:
        return Block("Unknown user.")
    changed = {a.get("reservation_id") for name in RESERVATION_WRITES for a in _prior_calls(conversation, name)}
    allowed: set[int] = set()
    for rid in user.reservations:
        res = db.reservations.get(rid)
        if res is None:
            continue
        eligible = (_value(user.membership) in ("silver", "gold") or _value(res.insurance) == "yes"
                    or _value(res.cabin) == "business")
        statuses = {_status(db, f.flight_number, f.date) for f in res.flights}
        if eligible and "cancelled" in statuses:
            allowed.add(100 * len(res.passengers))
        if eligible and "delayed" in statuses and rid in changed:
            allowed.add(50 * len(res.passengers))
    if int(call.arguments.get("amount", -1)) not in allowed:
        return Block("No compensation is allowed here: a certificate needs an eligible customer (silver/gold, "
                     "insured or business) and a cancelled flight ($100 x passengers) or a delayed flight "
                     "($50 x passengers, only after changing or cancelling that reservation).")
    return ALLOW


# insurance_at_booking_only (PRD §8) needs no gate: no tool can add insurance to an existing reservation.
GATES: list[Callable] = [
    auth_read_before_write, basic_economy_no_modify, modify_same_route, cabin_change_rules, cancel_eligibility,
    passenger_count_fixed, bags_add_only_and_price, payment_rules, booking_limits, compensation_eligibility_and_amount,
]


def check(call, db, conversation) -> tuple[Decision, str]:
    """Run every gate on one proposed call. Returns (decision, name of the gate that blocked or '')."""
    if call.name not in WRITE_TOOLS:
        return ALLOW, ""
    for gate in GATES:
        decision = gate(call, db, conversation)
        if isinstance(decision, Block):
            return decision, gate.__name__
    return ALLOW, ""
