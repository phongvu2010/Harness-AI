"""Deterministic MockLLMProvider for offline tests, demos, and evals."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Callable

from harness.core.models import (
    Message,
    ModelConfig,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolSpec,
)
from harness.providers.base import ProviderRegistry


class MockLLMProvider:
    """Scriptable mock provider returning queued responses or invoking a handler."""

    def __init__(
        self,
        responses: list[ModelResponse] | None = None,
        handler: Callable[[list[Message], list[ToolSpec] | None], ModelResponse]
        | None = None,
        stream_chunk_size: int = 8,
    ) -> None:
        self._queue: deque[ModelResponse] = deque(responses or [])
        self._handler = handler
        self.stream_chunk_size = max(1, stream_chunk_size)
        self.calls: list[list[Message]] = []

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> ModelResponse:
        self.calls.append(list(messages))
        if self._handler is not None:
            return self._handler(messages, tools)
        if self._queue:
            return self._queue.popleft()

        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        return ModelResponse(
            content=f"[MockLLM] Echo: {last_user}",
            usage=TokenUsage(input_tokens=10, output_tokens=10, cost_usd=0.0),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.generate(messages=messages, tools=tools, config=config)
        if response.content and not response.tool_calls:
            text = response.content
            step = self.stream_chunk_size
            for start in range(0, len(text), step):
                yield StreamChunk(delta=text[start : start + step])
        yield StreamChunk(response=response)


ProviderRegistry.register("mock", lambda _cfg: MockLLMProvider())
