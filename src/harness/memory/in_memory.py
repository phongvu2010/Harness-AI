"""Default InMemoryStore for development, testing, and lightweight runs."""

from __future__ import annotations

import re

from harness.core.state import SessionState
from harness.memory.base import EpisodeRecord

_TOKEN_RE = re.compile(r"[a-z0-9_-]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


class InMemoryStore:
    """In-memory implementation of MemoryStore with keyword-overlap episode search."""

    def __init__(self) -> None:
        self._episodes: list[EpisodeRecord] = []
        self._states: dict[str, SessionState] = {}

    async def add_episode(self, episode: EpisodeRecord) -> None:
        if not episode.verified:
            return  # Memory poisoning guard: ignore unverified lessons
        self._episodes.append(episode)

    async def search_episodes(
        self, query: str, limit: int = 3
    ) -> list[EpisodeRecord]:
        if not query.strip() or not self._episodes:
            return []
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        scored: list[tuple[float, EpisodeRecord]] = []
        for ep in self._episodes:
            text_terms = _tokenize(f"{ep.context_summary} {ep.lesson}")
            overlap = len(query_terms & text_terms)
            if overlap > 0:
                scored.append((overlap * ep.confidence_score, ep))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [ep for _, ep in scored[:limit]]

    async def save_state(self, session_id: str, state: SessionState) -> None:
        self._states[session_id] = state.model_copy(deep=True)

    async def load_state(self, session_id: str) -> SessionState | None:
        state = self._states.get(session_id)
        return state.model_copy(deep=True) if state is not None else None
