"""MemoryStore protocol and Episodic Memory record schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from harness.core.state import SessionState


class EpisodeRecord(BaseModel):
    """Verified lesson learned from a past execution."""

    id: str
    context_summary: str
    lesson: str
    verified: bool = True
    confidence_score: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryStore(Protocol):
    """Contract for session state persistence and episodic memory retrieval."""

    async def add_episode(self, episode: EpisodeRecord) -> None: ...

    async def search_episodes(
        self, query: str, limit: int = 3
    ) -> list[EpisodeRecord]: ...

    async def save_state(self, session_id: str, state: SessionState) -> None: ...

    async def load_state(self, session_id: str) -> SessionState | None: ...
