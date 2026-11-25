"""LLMProvider protocol and plug-and-play ProviderRegistry."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol

from harness.core.models import (
    Message,
    ModelConfig,
    ModelResponse,
    StreamChunk,
    ToolSpec,
)


class LLMProvider(Protocol):
    """Contract that every LLM adapter must implement."""

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> ModelResponse: ...

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> AsyncIterator[StreamChunk]: ...


ProviderFactory = Callable[[ModelConfig], LLMProvider]


class ProviderRegistry:
    """Registry mapping provider names to adapter factories."""

    _factories: dict[str, ProviderFactory] = {}
    _builtins_loaded: bool = False

    @classmethod
    def _ensure_builtins(cls) -> None:
        if cls._builtins_loaded:
            return
        cls._builtins_loaded = True
        import harness.providers.gemini  # noqa: F401
        import harness.providers.mock  # noqa: F401

    @classmethod
    def register(cls, name: str, factory: ProviderFactory) -> None:
        cls._factories[name.lower()] = factory

    @classmethod
    def create(cls, name: str, config: ModelConfig) -> LLMProvider:
        cls._ensure_builtins()
        key = name.lower()
        if key not in cls._factories:
            available = ", ".join(sorted(cls._factories)) or "(none)"
            raise ValueError(
                f"Unknown LLM provider '{name}'. Available providers: {available}"
            )
        return cls._factories[key](config)

    @classmethod
    def list_providers(cls) -> list[str]:
        cls._ensure_builtins()
        return sorted(cls._factories)
