"""Tests for InMemoryStore, ContextManager, and TraceLogger."""

from __future__ import annotations

import json
from pathlib import Path

from harness.core.context import ContextManager
from harness.core.models import Message, ModelResponse, Role, TokenUsage, ToolCall
from harness.core.state import SessionState
from harness.memory.base import EpisodeRecord
from harness.memory.in_memory import InMemoryStore
from harness.observability.tracer import TraceLogger


async def test_memory_store_ignores_unverified_episodes_and_persists_state() -> None:
    store = InMemoryStore()
    await store.add_episode(
        EpisodeRecord(
            id="bad",
            context_summary="billing timeout",
            lesson="never retry",
            verified=False,
        )
    )
    await store.add_episode(
        EpisodeRecord(
            id="good",
            context_summary="billing timeout",
            lesson="use exponential backoff",
            verified=True,
        )
    )

    found = await store.search_episodes("billing timeout")
    assert len(found) == 1
    assert found[0].id == "good"

    state = SessionState(session_id="sess-1", messages=[Message(role=Role.USER, content="hi")])
    await store.save_state("sess-1", state)
    loaded = await store.load_state("sess-1")
    assert loaded is not None
    assert loaded.messages[0].content == "hi"


def test_context_manager_compacts_with_user_summary_and_preserves_tool_pairs(
    tmp_path: Path,
) -> None:
    cm = ContextManager(
        base_system_prompt="System.",
        short_term_window=2,
        tool_output_max_chars=10,
        offload_dir=tmp_path,
    )
    state = SessionState(
        messages=[
            Message(role=Role.USER, content="m1"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="tc-1", name="ping", arguments={})],
            ),
            Message(role=Role.TOOL, content="pong", tool_call_id="tc-1", name="ping"),
            Message(role=Role.ASSISTANT, content="done"),
        ]
    )
    prepared = cm.prepare_messages(state)
    assert prepared[0].role == Role.SYSTEM
    assert prepared[1].role == Role.USER
    assert "omitted" in prepared[1].content
    # Ensure ASSISTANT(tool_calls) and TOOL remain paired together
    assert prepared[2].role == Role.ASSISTANT
    assert prepared[3].role == Role.TOOL
    assert prepared[4].role == Role.ASSISTANT

    offloaded = cm.offload_tool_output("tc-99", "x" * 50)
    assert "full output saved to" in offloaded
    assert (tmp_path / "tc-99.txt").read_text(encoding="utf-8") == "x" * 50


def test_trace_logger_writes_jsonl(tmp_path: Path) -> None:
    tracer = TraceLogger(tmp_path)
    path = tracer.log_step(
        run_id="run-123",
        step=1,
        messages=[Message(role=Role.USER, content="hi")],
        response=ModelResponse(
            content="hello", usage=TokenUsage(input_tokens=2, output_tokens=3)
        ),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["run_id"] == "run-123"
    assert data["response"]["content"] == "hello"


async def test_compact_if_needed_preserves_tool_pairs() -> None:
    from harness.core.models import ModelConfig
    from harness.providers.mock import MockLLMProvider

    cm = ContextManager(
        short_term_window=2,
        compaction_threshold_messages=3,
    )
    state = SessionState(
        messages=[
            Message(role=Role.USER, content="m1"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="tc-1", name="ping", arguments={})],
            ),
            Message(role=Role.TOOL, content="pong", tool_call_id="tc-1", name="ping"),
            Message(role=Role.ASSISTANT, content="done"),
        ]
    )
    provider = MockLLMProvider(responses=[ModelResponse(content="User asked m1.")])
    compacted = await cm.compact_if_needed(state, provider, ModelConfig())

    assert compacted is True
    assert state.messages[1].role == Role.ASSISTANT
    assert state.messages[2].role == Role.TOOL
    assert state.messages[3].role == Role.ASSISTANT


async def test_compact_if_needed_includes_tool_calls_in_transcript() -> None:
    from harness.core.models import ModelConfig
    from harness.providers.mock import MockLLMProvider

    cm = ContextManager(
        short_term_window=1,
        compaction_threshold_messages=2,
    )
    state = SessionState(
        messages=[
            Message(role=Role.USER, content="check billing"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        name="get_project_status",
                        arguments={"project": "billing-api"},
                    )
                ],
            ),
            Message(
                role=Role.TOOL,
                content="healthy",
                tool_call_id="tc-1",
                name="get_project_status",
            ),
            Message(role=Role.ASSISTANT, content="billing-api is healthy."),
        ]
    )
    provider = MockLLMProvider(responses=[ModelResponse(content="Checked billing-api.")])
    compacted = await cm.compact_if_needed(state, provider, ModelConfig())

    assert compacted is True
    summary_input = provider.calls[0][-1].content
    assert "get_project_status" in summary_input
    assert "billing-api" in summary_input


async def test_json_file_memory_store_concurrent_add_episode(tmp_path: Path) -> None:
    import asyncio

    from harness.memory.file_store import JSONFileMemoryStore

    store = JSONFileMemoryStore(tmp_path)
    await asyncio.gather(
        *(
            store.add_episode(
                EpisodeRecord(
                    id=f"ep-{i}",
                    context_summary="billing timeout",
                    lesson=f"lesson {i}",
                    verified=True,
                )
            )
            for i in range(10)
        )
    )
    found = await store.search_episodes("billing timeout", limit=20)
    assert len(found) == 10
