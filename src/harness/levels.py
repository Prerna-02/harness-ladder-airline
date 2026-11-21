"""Harness levels H0..H3, each built from named switches so any level can be rebuilt exactly.

Each level includes everything in the level below it (PRD section 7). Only the switches differ between levels;
model, customer simulator, tasks and seeds stay fixed (golden rule 1).
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Switches:
    structured_rules: bool = False  # H1: policy rewritten as short numbered rules
    task_checklist: bool = False  # H1: running checklist the agent restates
    code_gates: bool = False  # H2: deterministic checks on each state-changing tool call
    gate_reason: bool = True  # H2: send the block reason back to the agent (extension RQ5 turns this off)
    confirm_before_write: bool = False  # H3: explicit customer "yes" before any write
    verify_after_write: bool = False  # H3: separate verifier checks the database after a write

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


_H1 = Switches(structured_rules=True, task_checklist=True)
_H2 = Switches(**{**_H1.as_dict(), "code_gates": True})
_H3 = Switches(**{**_H2.as_dict(), "confirm_before_write": True, "verify_after_write": True})

LEVELS: dict[str, Switches] = {"H0": Switches(), "H1": _H1, "H2": _H2, "H3": _H3}


def tau2_agent_name(level: str) -> str:
    """The tau2 agent implementation that runs this level.

    H0 is tau2's own `llm_agent` unchanged: policy text in the system prompt, tools, no extra rails.
    """
    switches = LEVELS[level]  # KeyError on an unknown level is intended
    if switches == Switches():
        return "llm_agent"
    # H1..H3 agents are built on Day 2 (PROGRESS.md) and registered with tau2's registry at runtime.
    raise NotImplementedError(f"{level} agent is not built yet (Day 2).")
