"""Canonical data models for provider-agnostic agent orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Normalized representation of a model's tool invocation request."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Normalized result returned from executing a tool."""

    tool_call_id: str
    name: str
    output: str
    is_error: bool = False


class ToolSpec(BaseModel):
    """Provider-agnostic tool specification sent to the LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    requires_approval: bool = False


class Message(BaseModel):
    """Canonical conversation message."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class TokenUsage(BaseModel):
    """Token and cost accounting for a model response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelConfig(BaseModel):
    """Configuration passed to an LLMProvider."""

    provider: str = "mock"
    model: str = "gemini-2.5-flash"
    temperature: float = 0.2
    max_tokens: int = 4096
    provider_options: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] | None = Field(default=None, exclude=True)


class ModelResponse(BaseModel):
    """Normalized response from an LLMProvider."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw: dict[str, Any] | None = None


class StreamChunk(BaseModel):
    """Incremental chunk yielded by LLMProvider.stream()."""

    delta: str = ""
    thinking: str = ""
    response: ModelResponse | None = None


class HarnessLimits(BaseModel):
    """Circuit-breaker and execution budget limits."""

    max_steps: int = 15
    max_tokens: int = 100_000
    max_cost_usd: float = 1.0
    timeout_seconds: float = 120.0
    turn_timeout_seconds: float = 300.0
    max_repeated_tool_calls: int = 3
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5


class AgentEvent(BaseModel):
    """Event emitted by AgentLoop during async streaming execution."""

    type: Literal[
        "thinking",
        "token_delta",
        "tool_start",
        "tool_end",
        "approval_required",
        "final_answer",
        "error",
    ]
    run_id: str
    step: int = 0
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    parsed_output: Any = None
    usage: TokenUsage | None = None
