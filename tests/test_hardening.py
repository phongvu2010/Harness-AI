"""Tests for P0/P1 hardening: pricing, thinking, turn timeout, parallel tools, persistent memory, and skill path safety."""

from __future__ import annotations

import asyncio
import sys
import time
import types as pytypes
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from harness.core.agent import AgentLoop
from harness.core.builder import HarnessBuilder
from harness.core.context import ContextManager
from harness.core.models import (
    HarnessLimits,
    Message,
    ModelConfig,
    ModelResponse,
    Role,
    TokenUsage,
    ToolCall,
)
from harness.core.state import SessionState
from harness.loaders.agents_dir import AgentsDirLoader
from harness.memory.base import EpisodeRecord
from harness.memory.file_store import JSONFileMemoryStore
from harness.providers.gemini import GeminiAdapter
from harness.providers.mock import MockLLMProvider
from harness.providers.pricing import estimate_cost_usd
from harness.tools.decorator import tool
from harness.tools.registry import ToolRegistry


class _OutSchema(BaseModel):
    ok: bool


def test_pricing_estimates_gemini_cost() -> None:
    cost = estimate_cost_usd("gemini-2.5-flash", input_tokens=1_000_000, output_tokens=500_000)
    assert abs(cost - 0.45) < 1e-9
    assert estimate_cost_usd("unknown-model", 1000, 1000) == 0.0


def test_gemini_adapter_extracts_thinking_cost_and_schema(monkeypatch) -> None:
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
    cfg = ModelConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )
    _, _, gen_config = adapter._build_request(
        [Message(role=Role.USER, content="hi")], None, cfg
    )
    assert gen_config["response_mime_type"] == "application/json"
    assert "response_json_schema" in gen_config

    fake_resp = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="reasoning...", thought=True),
                        SimpleNamespace(text='{"ok": true}', thought=False),
                    ]
                )
            )
        ],
        usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=500),
    )
    assert adapter._extract_thinking(fake_resp) == "reasoning..."
    assert adapter._extract_text(fake_resp) == '{"ok": true}'
    usage = adapter._extract_usage(fake_resp, model_name="gemini-2.5-flash")
    assert usage.cost_usd > 0.0


async def test_turn_timeout_bounds_entire_turn() -> None:
    @tool(description="Slow step.")
    async def slow_step() -> str:
        await asyncio.sleep(0.06)
        return "ok"

    registry = ToolRegistry()
    registry.register(slow_step)

    provider = MockLLMProvider(
        handler=lambda _msgs, _tools: ModelResponse(
            tool_calls=[ToolCall(id="1", name="slow_step", arguments={})]
        )
    )
    agent = AgentLoop(
        provider=provider,
        tool_registry=registry,
        limits=HarnessLimits(
            max_steps=10,
            timeout_seconds=1.0,
            turn_timeout_seconds=0.1,
            max_repeated_tool_calls=20,
        ),
    )
    event = await agent.run(user_input="go")
    assert event.type == "error"
    assert "Turn timed out" in event.content


async def test_parallel_tool_execution_runs_concurrently() -> None:
    @tool(description="Async sleep.")
    async def wait_a() -> str:
        await asyncio.sleep(0.08)
        return "a"

    @tool(description="Async sleep.")
    async def wait_b() -> str:
        await asyncio.sleep(0.08)
        return "b"

    registry = ToolRegistry()
    registry.register_many([wait_a, wait_b])

    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                tool_calls=[
                    ToolCall(id="t1", name="wait_a", arguments={}),
                    ToolCall(id="t2", name="wait_b", arguments={}),
                ]
            ),
            ModelResponse(content="both done"),
        ]
    )
    agent = AgentLoop(provider=provider, tool_registry=registry)
    start = time.monotonic()
    event = await agent.run(user_input="run both")
    elapsed = time.monotonic() - start

    assert event.type == "final_answer"
    assert elapsed < 0.14


async def test_structured_output_repair_cleans_intermediate_messages() -> None:
    provider = MockLLMProvider(
        responses=[
            ModelResponse(content="invalid"),
            ModelResponse(content='{"ok": true}'),
        ]
    )
    state = SessionState()
    agent = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    event = await agent.run(
        user_input="check", state=state, response_schema=_OutSchema
    )

    assert event.type == "final_answer"
    assert [m.role for m in state.messages] == [Role.USER, Role.ASSISTANT]
    assert state.messages[1].content == '{"ok": true}'


async def test_compaction_records_usage_and_uses_user_role() -> None:
    cm = ContextManager(short_term_window=2, compaction_threshold_messages=3)
    state = SessionState(
        messages=[
            Message(role=Role.USER, content="m1"),
            Message(role=Role.ASSISTANT, content="a1"),
            Message(role=Role.USER, content="m2"),
            Message(role=Role.ASSISTANT, content="a2"),
        ]
    )
    provider = MockLLMProvider(
        responses=[
            ModelResponse(
                content="Summary.",
                usage=TokenUsage(input_tokens=50, output_tokens=10, cost_usd=0.01),
            )
        ]
    )
    compacted = await cm.compact_if_needed(state, provider, ModelConfig())
    assert compacted is True
    assert state.messages[0].role == Role.USER
    assert state.total_tokens == 60
    assert state.total_cost_usd == 0.01


async def test_json_file_memory_store_and_builder_wiring(tmp_path: Path) -> None:
    persist_dir = tmp_path / "mem"
    agent = HarnessBuilder(
        config={
            "llm": {"provider": "mock"},
            "memory": {"persist_dir": str(persist_dir)},
            "observability": {"enable_tracing": False},
        }
    ).build()

    assert isinstance(agent.memory, JSONFileMemoryStore)
    await agent.memory.add_episode(
        EpisodeRecord(
            id="ep-1",
            context_summary="Check billing-api?",
            lesson="Verify port 8080.",
            verified=True,
        )
    )
    reloaded = JSONFileMemoryStore(persist_dir)
    found = await reloaded.search_episodes("billing-api")
    assert len(found) == 1
    assert found[0].id == "ep-1"


async def test_read_skill_supports_subfiles_and_blocks_traversal(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "sec-skill"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sec-skill\ndescription: Security.\n---\nMain.", encoding="utf-8"
    )
    (refs_dir / "guide.md").write_text("Reference guide.", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")

    loader = AgentsDirLoader(tmp_path)
    bundle = loader.load()
    read_skill_tool = loader.make_read_skill_tool(bundle.skills)
    registry = ToolRegistry()
    registry.register(read_skill_tool)

    ok_res = await registry.execute(
        ToolCall(
            id="1",
            name="read_skill",
            arguments={"skill_name": "sec-skill", "file_path": "references/guide.md"},
        )
    )
    assert ok_res.output == "Reference guide."

    blocked_res = await registry.execute(
        ToolCall(
            id="2",
            name="read_skill",
            arguments={"skill_name": "sec-skill", "file_path": "../../secret.txt"},
        )
    )
    assert "Access denied" in blocked_res.output


async def test_json_file_memory_store_blocks_path_traversal(tmp_path: Path) -> None:
    import pytest

    from harness.core.state import SessionState
    from harness.memory.file_store import JSONFileMemoryStore

    store = JSONFileMemoryStore(tmp_path)
    state = SessionState(session_id="valid_1")
    await store.save_state("valid_1", state)
    assert await store.load_state("valid_1") is not None

    for bad_id in ["", "..", "../evil", "sub/dir", "/tmp/evil", "a..b"]:
        with pytest.raises(ValueError, match="Invalid session_id"):
            await store.save_state(bad_id, state)
        with pytest.raises(ValueError, match="Invalid session_id"):
            await store.load_state(bad_id)


def test_gemini_adapter_preserves_thought_signature_roundtrip(monkeypatch) -> None:
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
    raw_sig = b"\x01\x02thought-sig\xff"
    fake_resp = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(
                                id="fc-1", name="get_status", args={"svc": "api"}
                            ),
                            thought_signature=raw_sig,
                        )
                    ]
                )
            )
        ]
    )
    tool_calls = adapter._extract_tool_calls(fake_resp)
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "fc-1"
    assert "thought_signature_b64" in tool_calls[0].metadata
    # Ensure JSON serializability for SessionState & TraceLogger
    tool_calls[0].model_dump_json()

    _, contents, _ = adapter._build_request(
        [
            Message(role=Role.USER, content="check"),
            Message(role=Role.ASSISTANT, content="", tool_calls=tool_calls),
        ],
        None,
        None,
    )
    assert contents[1]["parts"][0]["thought_signature"] == raw_sig


async def test_agent_loop_retries_transient_errors_and_stops_on_non_transient() -> None:
    class TransientError(Exception):
        def __init__(self, code: int) -> None:
            super().__init__(f"HTTP {code}")
            self.code = code

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, tools=None, config=None):
            self.calls += 1
            if self.calls < 3:
                raise TransientError(429)
            return ModelResponse(content="recovered")

    provider = FlakyProvider()
    agent = AgentLoop(
        provider=provider,
        tool_registry=ToolRegistry(),
        limits=HarnessLimits(max_retries=2, retry_backoff_seconds=0.0),
    )
    event = await agent.run("hello")
    assert event.type == "final_answer"
    assert event.content == "recovered"
    assert provider.calls == 3

    class FatalProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, tools=None, config=None):
            self.calls += 1
            raise TransientError(400)

    fatal = FatalProvider()
    agent_fatal = AgentLoop(
        provider=fatal,
        tool_registry=ToolRegistry(),
        limits=HarnessLimits(max_retries=2, retry_backoff_seconds=0.0),
    )
    err_event = await agent_fatal.run("hello")
    assert err_event.type == "error"
    assert fatal.calls == 1


async def test_agent_loop_does_not_retry_stream_after_partial_chunks() -> None:
    from harness.core.models import StreamChunk

    class PartialFailStreamProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, tools=None, config=None):
            raise AssertionError("generate should not be called")

        async def stream(self, messages, tools=None, config=None):
            self.calls += 1
            yield StreamChunk(delta="partial ")
            raise ConnectionError("dropped mid-stream")

    provider = PartialFailStreamProvider()
    agent = AgentLoop(
        provider=provider,
        tool_registry=ToolRegistry(),
        limits=HarnessLimits(max_retries=2, retry_backoff_seconds=0.0),
    )
    events = [ev async for ev in agent.stream("hello")]
    assert provider.calls == 1
    assert [ev.type for ev in events] == ["token_delta", "error"]
