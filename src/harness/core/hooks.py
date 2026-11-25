"""Lifecycle hooks / middleware for cross-cutting concerns."""

from __future__ import annotations

from typing import Protocol

from harness.core.models import Message, ModelResponse, ToolCall, ToolResult
from harness.core.state import SessionState


class HarnessHook(Protocol):
    """Optional lifecycle callbacks invoked by AgentLoop."""

    async def before_model_call(
        self, state: SessionState, messages: list[Message]
    ) -> list[Message]: ...

    async def after_model_call(
        self, state: SessionState, response: ModelResponse
    ) -> None: ...

    async def before_tool_call(
        self, state: SessionState, tool_call: ToolCall
    ) -> ToolCall: ...

    async def after_tool_call(
        self, state: SessionState, tool_result: ToolResult
    ) -> ToolResult: ...

    async def on_error(self, state: SessionState, error: Exception) -> None: ...


class BaseHook:
    """No-op base class so custom hooks only override what they need."""

    async def before_model_call(
        self, state: SessionState, messages: list[Message]
    ) -> list[Message]:
        return messages

    async def after_model_call(
        self, state: SessionState, response: ModelResponse
    ) -> None:
        return None

    async def before_tool_call(
        self, state: SessionState, tool_call: ToolCall
    ) -> ToolCall:
        return tool_call

    async def after_tool_call(
        self, state: SessionState, tool_result: ToolResult
    ) -> ToolResult:
        return tool_result

    async def on_error(self, state: SessionState, error: Exception) -> None:
        return None


class HookManager:
    """Runs registered hooks in order."""

    def __init__(self, hooks: list[HarnessHook] | None = None) -> None:
        self._hooks: list[HarnessHook] = list(hooks or [])

    def register(self, hook: HarnessHook) -> None:
        self._hooks.append(hook)

    async def run_before_model_call(
        self, state: SessionState, messages: list[Message]
    ) -> list[Message]:
        current = messages
        for hook in self._hooks:
            current = await hook.before_model_call(state, current)
        return current

    async def run_after_model_call(
        self, state: SessionState, response: ModelResponse
    ) -> None:
        for hook in self._hooks:
            await hook.after_model_call(state, response)

    async def run_before_tool_call(
        self, state: SessionState, tool_call: ToolCall
    ) -> ToolCall:
        current = tool_call
        for hook in self._hooks:
            current = await hook.before_tool_call(state, current)
        return current

    async def run_after_tool_call(
        self, state: SessionState, tool_result: ToolResult
    ) -> ToolResult:
        current = tool_result
        for hook in self._hooks:
            current = await hook.after_tool_call(state, current)
        return current

    async def run_on_error(self, state: SessionState, error: Exception) -> None:
        for hook in self._hooks:
            await hook.on_error(state, error)
