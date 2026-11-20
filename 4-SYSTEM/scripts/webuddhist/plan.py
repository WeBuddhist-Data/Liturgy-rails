"""Plans: every write is first listed, then (only with --yes) sent, step by step.

A NotePlan is the ordered list of API calls for one note. Steps of a note run in
order and the note stops at its first failure (later steps depend on earlier
ones); the run then continues with the next note. Each step records its own
result in the ledger, so a failed or interrupted run can simply be repeated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .api import ApiError


@dataclass
class Step:
    label: str                      # what is sent, e.g. "PUT /v2/recordings/X/cues  (7 cues)"
    run: Callable[[], object]       # does it; returns a short outcome text (or None)


@dataclass
class NotePlan:
    title: str                      # short name shown to the user
    link: str                       # Obsidian link for reports
    steps: list = field(default_factory=list)
    problem: str | None = None      # why nothing can be done for this note (shown, not sent)


def execute(plans, *, yes: bool) -> dict:
    """Print every plan; run the steps only if `yes`. -> summary with report lines."""
    out = {"planned": 0, "done": 0, "failed": 0, "problems": 0, "lines": []}
    for p in plans:
        if p.problem:
            out["problems"] += 1
            print(f"skip   {p.title}\n         {p.problem}")
            out["lines"].append(f"- {p.link}: **not done**: {p.problem}")
            continue
        if not p.steps:
            continue
        out["planned"] += 1
        print(f"{'send ' if yes else 'plan '}  {p.title}")
        results = []
        for s in p.steps:
            print(f"         {s.label}")
            if not yes:
                continue
            try:
                got = s.run()
            except ApiError as exc:
                out["failed"] += 1
                print(f"           FAILED: {exc}")
                results.append(f"FAILED at `{s.label}`: {exc}".replace("\n", " "))
                break
            if got:
                print(f"           {got}")
            results.append(f"`{s.label}`" + (f": {got}" if got else ""))
        else:
            if yes:
                out["done"] += 1
        if yes:
            out["lines"].append(f"- {p.link}: " + "; ".join(results))
        else:
            out["lines"].append(f"- {p.link}: " + "; ".join(f"`{s.label}`" for s in p.steps))
    return out
