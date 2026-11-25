"""Deterministic scorers for Harness AI evals."""

from __future__ import annotations

from harness.core.models import AgentEvent


def contains_substring(event: AgentEvent, expected: str) -> bool:
    """Return True when the final answer contains the expected substring."""
    if event.type != "final_answer":
        return False
    return expected.lower() in event.content.lower()
