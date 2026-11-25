"""Session state management supporting Pause/Resume for human-in-the-loop."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from harness.core.models import Message, ToolCall


class SessionState(BaseModel):
    """Mutable execution state for a single agent session."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    messages: list[Message] = Field(default_factory=list)
    steps: int = 0
    turn_steps: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    paused: bool = False
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    recent_tool_signatures: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def record_usage(self, tokens: int, cost_usd: float) -> None:
        self.total_tokens += tokens
        self.total_cost_usd += cost_usd

    def start_new_turn(self) -> None:
        """Reset per-turn counters while preserving session history and cumulative usage."""
        self.run_id = uuid.uuid4().hex
        self.turn_steps = 0
        self.recent_tool_signatures.clear()
