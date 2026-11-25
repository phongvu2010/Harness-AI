"""Async AgentLoop with streaming events, circuit breakers, HITL, and structured output repair."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from harness.core.context import ContextManager
from harness.core.exceptions import (
    BudgetExceededError,
    HarnessError,
    LoopDetectedError,
    StructuredOutputError,
)
from harness.core.hooks import HookManager
from harness.core.models import (
    AgentEvent,
    HarnessLimits,
    Message,
    ModelConfig,
    ModelResponse,
    Role,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from harness.core.state import SessionState
from harness.memory.base import MemoryStore
from harness.observability.tracer import TraceLogger
from harness.providers.base import LLMProvider
from harness.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from harness.tools.mcp_bridge import MCPBridge

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_transient_error(exc: Exception) -> bool:
    """Return True for network/timeout/rate-limit errors safe to retry."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUS_CODES


class AgentLoop:
    """Core provider-agnostic execution loop."""

    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        context_manager: ContextManager | None = None,
        model_config: ModelConfig | None = None,
        limits: HarnessLimits | None = None,
        hooks: HookManager | None = None,
        memory: MemoryStore | None = None,
        tracer: TraceLogger | None = None,
        mcp_bridge: MCPBridge | None = None,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.context_manager = context_manager or ContextManager()
        self.model_config = model_config or ModelConfig()
        self.limits = limits or HarnessLimits()
        self.hooks = hooks or HookManager()
        self.memory = memory
        self.tracer = tracer
        self.mcp_bridge = mcp_bridge

    async def __aenter__(self) -> AgentLoop:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release resources such as persistent MCP server connections."""
        if self.mcp_bridge is not None:
            await self.mcp_bridge.aclose()

    close = aclose

    async def run(
        self,
        user_input: str | None = None,
        state: SessionState | None = None,
        response_schema: type[BaseModel] | None = None,
        approve_pending: bool | None = None,
    ) -> AgentEvent:
        """Execute until a terminal event (final_answer, approval_required, or error)."""
        last_event: AgentEvent | None = None
        async for event in self.stream(
            user_input=user_input,
            state=state,
            response_schema=response_schema,
            approve_pending=approve_pending,
        ):
            last_event = event
        assert last_event is not None
        return last_event

    async def stream(
        self,
        user_input: str | None = None,
        state: SessionState | None = None,
        response_schema: type[BaseModel] | None = None,
        approve_pending: bool | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Async generator yielding AgentEvents in real time."""
        session = state or SessionState()
        is_resuming = session.paused and bool(session.pending_tool_calls)
        if not is_resuming:
            session.start_new_turn()

        repair_attempts = 0
        max_repair_attempts = 2

        try:
            async with asyncio.timeout(self.limits.turn_timeout_seconds):
                # Handle resume from human-in-the-loop pause
                if is_resuming:
                    if approve_pending is None:
                        yield AgentEvent(
                            type="approval_required",
                            run_id=session.run_id,
                            step=session.turn_steps,
                            content="Pending tool calls still require approval.",
                            tool_call=session.pending_tool_calls[0],
                        )
                        return

                    pending = list(session.pending_tool_calls)
                    session.pending_tool_calls.clear()
                    session.paused = False

                    for idx, tc in enumerate(pending):
                        if idx > 0 and self.tool_registry.requires_approval(tc.name):
                            session.paused = True
                            session.pending_tool_calls = pending[idx:]
                            if self.memory is not None:
                                await self.memory.save_state(session.session_id, session)
                            yield AgentEvent(
                                type="approval_required",
                                run_id=session.run_id,
                                step=session.turn_steps,
                                content=f"Approval required for tool '{tc.name}'.",
                                tool_call=tc,
                            )
                            return

                        if approve_pending:
                            async for ev in self._execute_single_tool(session, tc):
                                yield ev
                        else:
                            declined = ToolResult(
                                tool_call_id=tc.id,
                                name=tc.name,
                                output="Tool execution declined by user.",
                                is_error=True,
                            )
                            session.add_message(
                                Message(
                                    role=Role.TOOL,
                                    content=declined.output,
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                )
                            )
                            yield AgentEvent(
                                type="tool_end",
                                run_id=session.run_id,
                                step=session.turn_steps,
                                tool_call=tc,
                                tool_result=declined,
                            )

                if user_input is not None:
                    session.add_message(Message(role=Role.USER, content=user_input))

                # Retrieve episodic memory once per turn (outside the tool-call loop)
                query_text = user_input or next(
                    (m.content for m in reversed(session.messages) if m.role == Role.USER),
                    "",
                )
                episodes = (
                    await self.memory.search_episodes(query_text, limit=3)
                    if (self.memory is not None and query_text)
                    else []
                )

                repair_start_idx: int | None = None
                while True:
                    self._check_budget(session)
                    session.turn_steps += 1
                    session.steps += 1

                    await self.context_manager.compact_if_needed(
                        session, provider=self.provider, config=self.model_config
                    )
                    messages = self.context_manager.prepare_messages(
                        session, episodes=episodes, response_schema=response_schema
                    )
                    messages = await self.hooks.run_before_model_call(session, messages)
                    tools = self.tool_registry.list_specs()

                    effective_config = (
                        self.model_config.model_copy(
                            update={"response_schema": response_schema.model_json_schema()}
                        )
                        if response_schema is not None
                        else self.model_config
                    )

                    response: ModelResponse | None = None
                    async for ev in self._call_provider(
                        session, messages, tools, config=effective_config
                    ):
                        if isinstance(ev, ModelResponse):
                            response = ev
                        else:
                            yield ev

                    assert response is not None
                    session.record_usage(
                        response.usage.total_tokens, response.usage.cost_usd
                    )
                    await self.hooks.run_after_model_call(session, response)

                    if self.tracer is not None:
                        self.tracer.log_step(
                            run_id=session.run_id,
                            step=session.turn_steps,
                            messages=messages,
                            response=response,
                        )

                    assistant_msg = Message(
                        role=Role.ASSISTANT,
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                    session.add_message(assistant_msg)

                    if response.tool_calls:
                        batch: list[ToolCall] = []
                        for idx, tc in enumerate(response.tool_calls):
                            self._check_loop(session, tc)
                            if self.tool_registry.requires_approval(tc.name):
                                if batch:
                                    async for ev in self._execute_tool_batch(session, batch):
                                        yield ev
                                    batch.clear()
                                session.paused = True
                                session.pending_tool_calls = response.tool_calls[idx:]
                                if self.memory is not None:
                                    await self.memory.save_state(session.session_id, session)
                                yield AgentEvent(
                                    type="approval_required",
                                    run_id=session.run_id,
                                    step=session.turn_steps,
                                    content=f"Approval required for tool '{tc.name}'.",
                                    tool_call=tc,
                                )
                                return
                            batch.append(tc)

                        if batch:
                            async for ev in self._execute_tool_batch(session, batch):
                                yield ev
                        continue

                    # No tool calls -> validate structured output if requested
                    parsed_output = None
                    if response_schema is not None:
                        cleaned = _FENCE_RE.sub("", response.content.strip()).strip()
                        try:
                            parsed_output = response_schema.model_validate_json(cleaned)
                        except ValidationError as exc:
                            if repair_attempts < max_repair_attempts:
                                if repair_start_idx is None:
                                    repair_start_idx = len(session.messages) - 1
                                repair_attempts += 1
                                session.add_message(
                                    Message(
                                        role=Role.USER,
                                        content=(
                                            f"Your output failed schema validation: {exc}. "
                                            "Please return ONLY valid JSON matching the required schema."
                                        ),
                                    )
                                )
                                continue
                            raise StructuredOutputError(
                                f"Failed structured output validation after {max_repair_attempts} retries: {exc}"
                            ) from exc

                        if repair_start_idx is not None:
                            # Replace intermediate repair attempts with only the valid final assistant message
                            session.messages = [
                                *session.messages[:repair_start_idx],
                                assistant_msg,
                            ]

                    if self.memory is not None:
                        await self.memory.save_state(session.session_id, session)

                    yield AgentEvent(
                        type="final_answer",
                        run_id=session.run_id,
                        step=session.turn_steps,
                        content=response.content,
                        parsed_output=parsed_output,
                        usage=response.usage,
                    )
                    return

        except TimeoutError:
            exc = HarnessError(
                f"Turn timed out after {self.limits.turn_timeout_seconds}s."
            )
            await self.hooks.run_on_error(session, exc)
            yield AgentEvent(
                type="error",
                run_id=session.run_id,
                step=session.turn_steps,
                content=str(exc),
            )
        except Exception as exc:
            await self.hooks.run_on_error(session, exc)
            yield AgentEvent(
                type="error",
                run_id=session.run_id,
                step=session.turn_steps,
                content=str(exc),
            )

    async def _call_provider(
        self,
        session: SessionState,
        messages: list[Message],
        tools: list[ToolSpec],
        config: ModelConfig | None = None,
    ) -> AsyncIterator[AgentEvent | ModelResponse]:
        active_config = config or self.model_config
        stream_fn = getattr(self.provider, "stream", None)
        max_attempts = 1 + max(0, self.limits.max_retries)

        for attempt in range(max_attempts):
            saw_chunk = False
            try:
                if callable(stream_fn):
                    final_resp: ModelResponse | None = None
                    deltas: list[str] = []
                    async with asyncio.timeout(self.limits.timeout_seconds):
                        async for chunk in stream_fn(
                            messages=messages,
                            tools=tools or None,
                            config=active_config,
                        ):
                            saw_chunk = True
                            thinking = getattr(chunk, "thinking", "")
                            if thinking:
                                yield AgentEvent(
                                    type="thinking",
                                    run_id=session.run_id,
                                    step=session.turn_steps,
                                    content=thinking,
                                )
                            if chunk.delta:
                                deltas.append(chunk.delta)
                                yield AgentEvent(
                                    type="token_delta",
                                    run_id=session.run_id,
                                    step=session.turn_steps,
                                    content=chunk.delta,
                                )
                            if chunk.response is not None:
                                final_resp = chunk.response
                    if final_resp is not None:
                        yield final_resp
                        return
                    if saw_chunk:
                        yield ModelResponse(content="".join(deltas))
                        return
                    raise HarnessError(
                        "Provider stream() completed without yielding any response."
                    )

                resp = await asyncio.wait_for(
                    self.provider.generate(
                        messages=messages,
                        tools=tools or None,
                        config=active_config,
                    ),
                    timeout=self.limits.timeout_seconds,
                )
                yield resp
                return
            except Exception as exc:
                can_retry = (
                    not saw_chunk
                    and attempt + 1 < max_attempts
                    and _is_transient_error(exc)
                )
                if not can_retry:
                    raise
                delay = self.limits.retry_backoff_seconds * (2**attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _run_tool_with_timeout(self, tc: ToolCall) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self.tool_registry.execute(tc),
                timeout=self.limits.timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                tool_call_id=tc.id,
                name=tc.name,
                output=f"Tool '{tc.name}' timed out after {self.limits.timeout_seconds}s.",
                is_error=True,
            )

    async def _execute_tool_batch(
        self, session: SessionState, batch: list[ToolCall]
    ) -> AsyncIterator[AgentEvent]:
        prepared: list[ToolCall] = []
        for tc in batch:
            tc = await self.hooks.run_before_tool_call(session, tc)
            prepared.append(tc)
            yield AgentEvent(
                type="tool_start",
                run_id=session.run_id,
                step=session.turn_steps,
                tool_call=tc,
            )

        if len(prepared) == 1:
            raw_results = [await self._run_tool_with_timeout(prepared[0])]
        else:
            raw_results = list(
                await asyncio.gather(
                    *(self._run_tool_with_timeout(tc) for tc in prepared)
                )
            )

        for tc, result in zip(prepared, raw_results, strict=True):
            result = ToolResult(
                tool_call_id=result.tool_call_id,
                name=result.name,
                output=self.context_manager.offload_tool_output(tc.id, result.output),
                is_error=result.is_error,
            )
            result = await self.hooks.run_after_tool_call(session, result)
            session.add_message(
                Message(
                    role=Role.TOOL,
                    content=result.output,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
            )
            yield AgentEvent(
                type="tool_end",
                run_id=session.run_id,
                step=session.turn_steps,
                tool_call=tc,
                tool_result=result,
            )

    async def _execute_single_tool(
        self, session: SessionState, tc: ToolCall
    ) -> AsyncIterator[AgentEvent]:
        async for ev in self._execute_tool_batch(session, [tc]):
            yield ev

    def _check_budget(self, session: SessionState) -> None:
        if session.turn_steps >= self.limits.max_steps:
            raise BudgetExceededError(
                f"Exceeded max_steps limit ({self.limits.max_steps})."
            )
        if session.total_tokens >= self.limits.max_tokens:
            raise BudgetExceededError(
                f"Exceeded max_tokens limit ({self.limits.max_tokens})."
            )
        if session.total_cost_usd >= self.limits.max_cost_usd:
            raise BudgetExceededError(
                f"Exceeded max_cost_usd limit (${self.limits.max_cost_usd:.4f})."
            )

    def _check_loop(self, session: SessionState, tc: ToolCall) -> None:
        sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
        session.recent_tool_signatures.append(sig)
        window = self.limits.max_repeated_tool_calls
        if len(session.recent_tool_signatures) >= window:
            tail = session.recent_tool_signatures[-window:]
            if all(item == sig for item in tail):
                raise LoopDetectedError(
                    f"Detected {window} consecutive identical calls to '{tc.name}'."
                )
