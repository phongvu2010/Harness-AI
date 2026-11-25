"""Sample ReflectorHook implementing verified-only episodic self-learning."""

from __future__ import annotations

import uuid

from harness.core.hooks import BaseHook
from harness.core.models import Message, ModelResponse, Role, ToolResult
from harness.core.state import SessionState
from harness.memory.base import EpisodeRecord, MemoryStore

_PENDING_ERR_KEY = "_reflector_pending_error"
_CANDIDATES_KEY = "_reflector_recovery_candidates"


class ReflectorHook(BaseHook):
    """Records a verified lesson into MemoryStore only after recovery and turn completion."""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    async def after_tool_call(
        self, state: SessionState, tool_result: ToolResult
    ) -> ToolResult:
        if tool_result.is_error:
            state.metadata[_PENDING_ERR_KEY] = {
                "tool": tool_result.name,
                "error": tool_result.output[:300],
            }
            return tool_result

        pending = state.metadata.pop(_PENDING_ERR_KEY, None)
        if pending:
            candidates = state.metadata.setdefault(_CANDIDATES_KEY, [])
            candidates.append(
                {
                    "context_summary": f"{_last_user_message(state.messages)} (failed on {pending['tool']})",
                    "lesson": (
                        f"Avoid error '{pending['error']}'; "
                        f"recovered using '{tool_result.name}'."
                    ),
                }
            )
        return tool_result

    async def after_model_call(
        self, state: SessionState, response: ModelResponse
    ) -> None:
        if response.tool_calls:
            return
        # Only commit verified lessons once the turn reaches a non-tool response without unresolved errors
        state.metadata.pop(_PENDING_ERR_KEY, None)
        candidates = state.metadata.pop(_CANDIDATES_KEY, [])
        for item in candidates:
            await self.memory.add_episode(
                EpisodeRecord(
                    id=uuid.uuid4().hex[:12],
                    context_summary=item["context_summary"],
                    lesson=item["lesson"],
                    verified=True,
                )
            )


def _last_user_message(messages: list[Message]) -> str:
    for msg in reversed(messages):
        if msg.role == Role.USER and msg.content:
            return msg.content[:120]
    return "general task"
