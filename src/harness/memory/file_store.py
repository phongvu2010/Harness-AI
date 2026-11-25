"""Lightweight JSON-file-backed MemoryStore for single-node persistence."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from harness.core.state import SessionState
from harness.memory.base import EpisodeRecord
from harness.memory.in_memory import _tokenize

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class JSONFileMemoryStore:
    """Persists verified episodes and session states as JSON files on disk."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self._episodes_file = self.base_dir / "episodes.json"
        self._states_dir = self.base_dir / "states"
        self._lock = asyncio.Lock()

    def _state_path(self, session_id: str) -> Path:
        """Validate session_id and return a resolved path confined to _states_dir."""
        if not session_id or ".." in session_id or not _SAFE_SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        states_root = self._states_dir.resolve()
        target = (states_root / f"{session_id}.json").resolve()
        if not target.is_relative_to(states_root):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        return target

    def _read_episodes(self) -> list[EpisodeRecord]:
        if not self._episodes_file.is_file():
            return []
        raw = json.loads(self._episodes_file.read_text(encoding="utf-8"))
        return [EpisodeRecord.model_validate(item) for item in raw]

    def _write_episodes(self, episodes: list[EpisodeRecord]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = [ep.model_dump(mode="json") for ep in episodes]
        tmp_path = self._episodes_file.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp_path.replace(self._episodes_file)

    async def add_episode(self, episode: EpisodeRecord) -> None:
        if not episode.verified:
            return
        async with self._lock:
            episodes = self._read_episodes()
            episodes.append(episode)
            self._write_episodes(episodes)

    async def search_episodes(
        self, query: str, limit: int = 3
    ) -> list[EpisodeRecord]:
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        async with self._lock:
            episodes = self._read_episodes()
        if not episodes:
            return []
        scored: list[tuple[float, EpisodeRecord]] = []
        for ep in episodes:
            text_terms = _tokenize(f"{ep.context_summary} {ep.lesson}")
            overlap = len(query_terms & text_terms)
            if overlap > 0:
                scored.append((overlap * ep.confidence_score, ep))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [ep for _, ep in scored[:limit]]

    async def save_state(self, session_id: str, state: SessionState) -> None:
        path = self._state_path(session_id)
        async with self._lock:
            self._states_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(
                state.model_dump_json(indent=2), encoding="utf-8"
            )
            tmp_path.replace(path)

    async def load_state(self, session_id: str) -> SessionState | None:
        path = self._state_path(session_id)
        async with self._lock:
            if not path.is_file():
                return None
            return SessionState.model_validate_json(path.read_text(encoding="utf-8"))
