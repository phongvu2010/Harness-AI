"""Tests for AgentLoop: tool calls, circuit breakers, HITL pause/resume, structured output."""

from __future__ import annotations

from pydantic import BaseModel

from harness.core.agent import AgentLoop
from harness.core.models import (
    HarnessLimits,
    ModelResponse,
    TokenUsage,
    ToolCall,
)
from harness.core.state import SessionState
from harness.providers.mock import MockLLMProvider
from harness.tools.decorator import tool
from harness.tools.registry import ToolRegistry


class SummarySchema(BaseModel):
    status: str
    count: int


async def test_agent_loop_executes_tool_and_returns_final_answer() -> None:
    @tool(description="Add two integers.")
    def add(a: int, b: int) -> int:
        return a + b

    registry = ToolRegistry()
    registry.register(add)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[ToolCall(id="tc-1", name="add", arguments={"a": 2, "b": 3})],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
            ModelResponse(
                content="The sum is 5.",
                usage=TokenUsage(input_tokens=15, output_tokens=6),
            ),
        ]
    )

    agent = AgentLoop(provider=provider, tool_registry=registry)
    event = await agent.run(user_input="What is 2 + 3?")

    assert event.type == "final_answer"
    assert event.content == "The sum is 5."
    assert event.step == 2


async def test_circuit_breaker_max_steps() -> None:
    @tool(description="Echo.")
    def echo(x: str) -> str:
        return x

    registry = ToolRegistry()
    registry.register(echo)

    provider = MockLLMProvider(
        handler=lambda _msgs, _tools: ModelResponse(
            tool_calls=[ToolCall(id="tc", name="echo", arguments={"x": "hi"})]
        )
    )
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(max_steps=2, max_repeated_tool_calls=10),
    )
    event = await agent.run(user_input="loop")

    assert event.type == "error"
    assert "max_steps" in event.content


async def test_circuit_breaker_loop_detector() -> None:
    @tool(description="Echo.")
    def echo(x: str) -> str:
        return x

    registry = ToolRegistry()
    registry.register(echo)

    provider = MockLLMProvider(
        handler=lambda _msgs, _tools: ModelResponse(
            tool_calls=[ToolCall(id="tc", name="echo", arguments={"x": "same"})]
        )
    )
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(max_steps=10, max_repeated_tool_calls=3),
    )
    event = await agent.run(user_input="loop")

    assert event.type == "error"
    assert "consecutive identical calls" in event.content


async def test_human_in_the_loop_pause_and_resume() -> None:
    executed: list[str] = []

    @tool(description="Dangerous delete.", requires_approval=True)
    def delete_item(item_id: str) -> str:
        executed.append(item_id)
        return f"deleted:{item_id}"

    registry = ToolRegistry()
    registry.register(delete_item)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[
                    ToolCall(id="tc-del", name="delete_item", arguments={"item_id": "42"})
                ]
            ),
            ModelResponse(content="Item 42 was deleted."),
        ]
    )

    state = SessionState()
    agent = AgentLoop(provider=provider, tool_registry=registry)

    paused_event = await agent.run(user_input="Delete 42", state=state)
    assert paused_event.type == "approval_required"
    assert state.paused is True
    assert executed == []

    resumed_event = await agent.run(state=state, approve_pending=True)
    assert resumed_event.type == "final_answer"
    assert resumed_event.run_id == paused_event.run_id
    assert resumed_event.content == "Item 42 was deleted."
    assert executed == ["42"]
    assert state.paused is False


async def test_multi_turn_resets_turn_steps_and_run_id() -> None:
    @tool(description="Ping.")
    def ping() -> str:
        return "pong"

    registry = ToolRegistry()
    registry.register(ping)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(tool_calls=[ToolCall(id="t1", name="ping", arguments={})]),
            ModelResponse(content="Turn 1 done"),
            ModelResponse(tool_calls=[ToolCall(id="t2", name="ping", arguments={})]),
            ModelResponse(content="Turn 2 done"),
        ]
    )
    state = SessionState()
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(max_steps=2),
    )

    ev1 = await agent.run(user_input="turn 1", state=state)
    ev2 = await agent.run(user_input="turn 2", state=state)

    assert ev1.type == "final_answer"
    assert ev2.type == "final_answer"
    assert ev1.run_id != ev2.run_id
    assert ev1.step == 2
    assert ev2.step == 2
    assert state.steps == 4


async def test_structured_output_auto_repair() -> None:
    provider = MockLLMProvider(
        responses=[
            ModelResponse(content="not valid json"),
            ModelResponse(content='```json\n{"status": "ok", "count": 7}\n```'),
        ]
    )
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    event = await agent.run(user_input="Give summary", response_schema=SummarySchema)

    assert event.type == "final_answer"
    assert isinstance(event.parsed_output, SummarySchema)
    assert event.parsed_output.status == "ok"
    assert event.parsed_output.count == 7


async def test_streaming_emits_token_deltas() -> None:
    provider = MockLLMProvider(
        responses=[ModelResponse(content="Hello streaming world")],
        stream_chunk_size=6,
    )
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())

    events = [ev async for ev in agent.stream(user_input="Hi")]
    deltas = [ev.content for ev in events if ev.type == "token_delta"]

    assert "".join(deltas) == "Hello streaming world"
    assert len(deltas) > 1
    assert events[-1].type == "final_answer"


async def test_reflector_hook_records_verified_lesson_after_recovery() -> None:
    from harness.core.builder import HarnessBuilder
    from harness.memory.in_memory import InMemoryStore

    @tool(description="Fail on negative, succeed on positive.")
    def divide_positive(n: int) -> int:
        if n <= 0:
            raise ValueError("n must be positive")
        return 100 // n

    registry = ToolRegistry()
    registry.register(divide_positive)
    memory = InMemoryStore()

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[
                    ToolCall(id="t1", name="divide_positive", arguments={"n": 0})
                ]
            ),
            ModelResponse(
                tool_calls=[
                    ToolCall(id="t2", name="divide_positive", arguments={"n": 5})
                ]
            ),
            ModelResponse(content="Result is 20."),
        ]
    )

    agent = await HarnessBuilder().abuild(
        provider=provider,
        tool_registry=registry,
        memory=memory,
    )
    async with agent:
        event = await agent.run(user_input="Compute division")

    assert event.type == "final_answer"
    episodes = await memory.search_episodes("Compute division")
    assert len(episodes) == 1
    assert episodes[0].verified is True
    assert "divide_positive" in episodes[0].lesson


def test_gemini_adapter_groups_tool_responses_and_merges_provider_options(
    monkeypatch,
) -> None:
    import sys
    import types as pytypes

    from harness.core.models import Message, ModelConfig, Role
    from harness.providers.gemini import GeminiAdapter

    fake_google = pytypes.ModuleType("google")
    fake_genai = pytypes.ModuleType("google.genai")
    fake_types = pytypes.ModuleType("google.genai.types")
    fake_types.FunctionDeclaration = lambda **kw: kw
    fake_types.Tool = lambda **kw: kw
    fake_types.GenerateContentConfig = lambda **kw: kw
    fake_genai.types = fake_types
    fake_genai.Client = lambda api_key: object()
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)

    adapter = GeminiAdapter(api_key="test-key")
    messages = [
        Message(role=Role.USER, content="Run two tools"),
        Message(
            role=Role.ASSISTANT,
            tool_calls=[
                ToolCall(id="1", name="t1", arguments={}),
                ToolCall(id="2", name="t2", arguments={}),
            ],
        ),
        Message(role=Role.TOOL, name="t1", content="r1", tool_call_id="1"),
        Message(role=Role.TOOL, name="t2", content="r2", tool_call_id="2"),
    ]
    cfg = ModelConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        temperature=0.2,
        provider_options={"temperature": 0.9},
    )

    model_name, contents, gen_config = adapter._build_request(messages, None, cfg)
    assert model_name == "gemini-2.5-flash"
    assert gen_config["temperature"] == 0.9
    assert len(contents) == 3
    assert len(contents[2]["parts"]) == 2


async def test_structured_output_injects_schema_on_first_call() -> None:
    provider = MockLLMProvider(
        responses=[ModelResponse(content='{"status": "healthy", "count": 1}')]
    )
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    event = await agent.run(user_input="Check", response_schema=SummarySchema)

    assert event.type == "final_answer"
    assert len(provider.calls) == 1
    first_call_text = "\n".join(m.content for m in provider.calls[0])
    assert '"status"' in first_call_text
    assert '"count"' in first_call_text


async def test_human_in_the_loop_multi_tool_batch_pauses_per_approval_tool() -> None:
    executed: list[str] = []

    @tool(description="Dangerous delete.", requires_approval=True)
    def delete_item(item_id: str) -> str:
        executed.append(item_id)
        return f"deleted:{item_id}"

    registry = ToolRegistry()
    registry.register(delete_item)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[
                    ToolCall(id="tc-1", name="delete_item", arguments={"item_id": "1"}),
                    ToolCall(id="tc-2", name="delete_item", arguments={"item_id": "2"}),
                ]
            ),
            ModelResponse(content="Both 1 and 2 deleted."),
        ]
    )

    state = SessionState()
    agent = AgentLoop(provider=provider, tool_registry=registry)

    pause1 = await agent.run(user_input="Delete 1 and 2", state=state)
    assert pause1.type == "approval_required"
    assert pause1.tool_call is not None and pause1.tool_call.id == "tc-1"
    assert executed == []

    pause2 = await agent.run(state=state, approve_pending=True)
    assert pause2.type == "approval_required"
    assert pause2.tool_call is not None and pause2.tool_call.id == "tc-2"
    assert executed == ["1"]

    final = await agent.run(state=state, approve_pending=True)
    assert final.type == "final_answer"
    assert final.content == "Both 1 and 2 deleted."
    assert executed == ["1", "2"]
    assert state.paused is False


async def test_multi_turn_resets_loop_detector_signatures() -> None:
    @tool(description="Ping.")
    def ping() -> str:
        return "pong"

    registry = ToolRegistry()
    registry.register(ping)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(tool_calls=[ToolCall(id="t1", name="ping", arguments={})]),
            ModelResponse(content="Turn 1"),
            ModelResponse(tool_calls=[ToolCall(id="t2", name="ping", arguments={})]),
            ModelResponse(content="Turn 2"),
            ModelResponse(tool_calls=[ToolCall(id="t3", name="ping", arguments={})]),
            ModelResponse(content="Turn 3"),
        ]
    )
    state = SessionState()
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(max_steps=5, max_repeated_tool_calls=3),
    )

    ev1 = await agent.run(user_input="t1", state=state)
    ev2 = await agent.run(user_input="t2", state=state)
    ev3 = await agent.run(user_input="t3", state=state)

    assert ev1.type == "final_answer"
    assert ev2.type == "final_answer"
    assert ev3.type == "final_answer"
    assert ev3.content == "Turn 3"


async def test_multi_turn_session_respects_per_run_max_steps() -> None:
    provider = MockLLMProvider(
        responses=[
            ModelResponse(content="turn 1"),
            ModelResponse(content="turn 2"),
            ModelResponse(content="turn 3"),
        ]
    )
    state = SessionState()
    agent = AgentLoop(
        provider=provider,
        tool_registry=ToolRegistry(),
        limits=HarnessLimits(max_steps=2),
    )

    for idx in range(1, 4):
        event = await agent.run(user_input=f"hi {idx}", state=state)
        assert event.type == "final_answer"
        assert event.content == f"turn {idx}"
        assert event.step == 1

    assert state.steps == 3


async def test_search_episodes_called_once_per_run() -> None:
    from harness.memory.in_memory import InMemoryStore

    class CountingMemory(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.search_calls = 0

        async def search_episodes(self, query: str, limit: int = 3):
            self.search_calls += 1
            return await super().search_episodes(query, limit=limit)

    @tool(description="Ping.")
    def ping() -> str:
        return "pong"

    registry = ToolRegistry()
    registry.register(ping)
    memory = CountingMemory()
    provider = MockLLMProvider(
        responses=[
            ModelResponse(tool_calls=[ToolCall(id="1", name="ping", arguments={})]),
            ModelResponse(tool_calls=[ToolCall(id="2", name="ping", arguments={})]),
            ModelResponse(content="done"),
        ]
    )

    agent = AgentLoop(provider=provider, tool_registry=registry, memory=memory)
    event = await agent.run(user_input="ping twice")

    assert event.type == "final_answer"
    assert memory.search_calls == 1


async def test_human_in_the_loop_decline_pending() -> None:
    executed: list[str] = []

    @tool(description="Dangerous delete.", requires_approval=True)
    def delete_item(item_id: str) -> str:
        executed.append(item_id)
        return f"deleted:{item_id}"

    registry = ToolRegistry()
    registry.register(delete_item)
    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[
                    ToolCall(id="tc-del", name="delete_item", arguments={"item_id": "42"})
                ]
            ),
            ModelResponse(content="Okay, I cancelled the deletion."),
        ]
    )

    state = SessionState()
    agent = AgentLoop(provider=provider, tool_registry=registry)

    paused = await agent.run(user_input="Delete 42", state=state)
    assert paused.type == "approval_required"

    declined = await agent.run(state=state, approve_pending=False)
    assert declined.type == "final_answer"
    assert "cancelled" in declined.content
    assert executed == []
    assert state.paused is False


async def test_structured_output_exhausts_retries_and_errors() -> None:
    provider = MockLLMProvider(
        responses=[
            ModelResponse(content="bad 1"),
            ModelResponse(content="bad 2"),
            ModelResponse(content="bad 3"),
        ]
    )
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    event = await agent.run(user_input="Give summary", response_schema=SummarySchema)

    assert event.type == "error"
    assert "Failed structured output validation" in event.content
    assert len(provider.calls) == 3


async def test_tool_execution_timeout_returns_error_result() -> None:
    import asyncio

    @tool(description="Slow tool.")
    async def slow_tool() -> str:
        await asyncio.sleep(1.0)
        return "done"

    registry = ToolRegistry()
    registry.register(slow_tool)

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[ToolCall(id="tc-slow", name="slow_tool", arguments={})]
            ),
            ModelResponse(content="Recovered after timeout."),
        ]
    )
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(timeout_seconds=0.05),
    )

    events = [ev async for ev in agent.stream(user_input="Run slow tool")]
    tool_end = next(ev for ev in events if ev.type == "tool_end")
    assert tool_end.tool_result is not None
    assert tool_end.tool_result.is_error is True
    assert "timed out" in tool_end.tool_result.output
    assert events[-1].type == "final_answer"
    assert events[-1].content == "Recovered after timeout."


async def test_agent_loop_close_alias_closes_mcp_bridge() -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    bridge = FakeBridge()
    agent = AgentLoop(
        provider=MockLLMProvider(),
        tool_registry=ToolRegistry(),
        mcp_bridge=bridge,  # type: ignore[arg-type]
    )
    await agent.close()
    assert bridge.closed is True


async def test_stream_without_final_response_does_not_double_call_generate() -> None:
    from collections.abc import AsyncIterator

    from harness.core.models import StreamChunk

    class DeltaOnlyStreamProvider:
        def __init__(self) -> None:
            self.generate_calls = 0

        async def generate(self, **_kwargs: object) -> ModelResponse:
            self.generate_calls += 1
            return ModelResponse(content="should not be called")

        async def stream(self, **_kwargs: object) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(delta="Hello ")
            yield StreamChunk(delta="world")

    provider = DeltaOnlyStreamProvider()
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    event = await agent.run(user_input="hi")

    assert event.type == "final_answer"
    assert event.content == "Hello world"
    assert provider.generate_calls == 0


async def test_harness_builder_wires_offload_dir(tmp_path: object) -> None:
    from pathlib import Path

    from harness.core.builder import HarnessBuilder

    offload_path = Path(str(tmp_path)) / "offload"
    agent = HarnessBuilder(
        config={
            "llm": {"provider": "mock"},
            "memory": {
                "tool_output_max_chars": 10,
                "offload_dir": str(offload_path),
            },
            "observability": {"enable_tracing": False},
        }
    ).build()

    assert agent.context_manager.offload_dir == offload_path
    truncated = agent.context_manager.offload_tool_output("tc-1", "a" * 30)
    assert "full output saved to" in truncated
    assert (offload_path / "tc-1.txt").read_text(encoding="utf-8") == "a" * 30


async def test_reflector_hook_ignores_text_only_apology_after_error() -> None:
    from harness.core.builder import HarnessBuilder
    from harness.memory.in_memory import InMemoryStore

    @tool(description="Always fails.")
    def broken_tool() -> str:
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(broken_tool)
    memory = InMemoryStore()

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[ToolCall(id="t1", name="broken_tool", arguments={})]
            ),
            ModelResponse(content="Sorry, I could not complete that."),
        ]
    )

    agent = HarnessBuilder(
        config={"llm": {"provider": "mock"}, "observability": {"enable_tracing": False}}
    ).build(provider=provider, tool_registry=registry, memory=memory)

    event = await agent.run(user_input="Run broken tool")
    assert event.type == "final_answer"
    assert await memory.search_episodes("Run broken tool") == []

