"""Tests for MCPBridge persistent sessions and HarnessBuilder async wiring."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.core.builder import HarnessBuilder
from harness.core.models import ToolCall
from harness.tools.mcp_bridge import MCPBridge
from harness.tools.registry import ToolRegistry


@dataclass
class _FakeMCPTool:
    name: str
    description: str
    inputSchema: dict[str, Any]


@dataclass
class _FakeTextContent:
    text: str


@dataclass
class _FakeCallResult:
    content: list[_FakeTextContent]
    isError: bool = False


@dataclass
class _FakeListResult:
    tools: list[_FakeMCPTool]


class _FakeSession:
    def __init__(self) -> None:
        self.initialized = 0
        self.listed = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def initialize(self) -> None:
        self.initialized += 1

    async def list_tools(self) -> _FakeListResult:
        self.listed += 1
        return _FakeListResult(
            tools=[
                _FakeMCPTool(
                    name="ping",
                    description="Ping MCP server.",
                    inputSchema={
                        "type": "object",
                        "properties": {"msg": {"type": "string"}},
                    },
                )
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeCallResult:
        self.calls.append((name, arguments))
        return _FakeCallResult(content=[_FakeTextContent(text=f"pong:{arguments.get('msg')}")])


async def test_mcp_bridge_reuses_persistent_session_and_closes() -> None:
    created_sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def _fake_transport():
        sess = _FakeSession()
        created_sessions.append(sess)
        try:
            yield sess
        finally:
            sess.closed = True

    async def _factory(
        stack: AsyncExitStack, server_name: str, cfg: dict[str, Any]
    ) -> _FakeSession:
        sess = await stack.enter_async_context(_fake_transport())
        await sess.initialize()
        return sess

    bridge = MCPBridge(
        servers_config={"demo": {"command": "echo"}},
        session_factory=_factory,
    )
    registry = ToolRegistry()

    count = await bridge.register_into(registry)
    assert count == 1
    assert len(created_sessions) == 1
    assert bridge.is_connected is True

    res1 = await registry.execute(
        ToolCall(id="1", name="demo__ping", arguments={"msg": "a"})
    )
    res2 = await registry.execute(
        ToolCall(id="2", name="demo__ping", arguments={"msg": "b"})
    )

    assert res1.output == "pong:a"
    assert res2.output == "pong:b"
    assert len(created_sessions) == 1
    assert created_sessions[0].initialized == 1
    assert created_sessions[0].calls == [("ping", {"msg": "a"}), ("ping", {"msg": "b"})]
    assert created_sessions[0].closed is False

    await bridge.close()
    assert bridge.is_connected is False
    assert created_sessions[0].closed is True


async def test_builder_abuild_wires_mcp_and_closes_on_exit(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".agent"
    agents_dir.mkdir()
    (agents_dir / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": []}}}),
        encoding="utf-8",
    )

    sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def _fake_transport():
        sess = _FakeSession()
        sessions.append(sess)
        try:
            yield sess
        finally:
            sess.closed = True

    async def _factory(
        stack: AsyncExitStack, server_name: str, cfg: dict[str, Any]
    ) -> _FakeSession:
        sess = await stack.enter_async_context(_fake_transport())
        await sess.initialize()
        return sess

    builder = HarnessBuilder(
        config={"llm": {"provider": "mock"}, "agents_dir": str(agents_dir)}
    )
    agent = await builder.abuild(mcp_session_factory=_factory)

    async with agent:
        spec_names = [s.name for s in agent.tool_registry.list_specs()]
        assert "fs__ping" in spec_names
        assert sessions[0].closed is False

    assert sessions[0].closed is True


async def test_mcp_bridge_propagates_is_error() -> None:
    class _ErrorSession(_FakeSession):
        async def call_tool(
            self, name: str, arguments: dict[str, Any]
        ) -> _FakeCallResult:
            return _FakeCallResult(
                content=[_FakeTextContent(text="permission denied")],
                isError=True,
            )

    wrapped = MCPBridge.wrap_session_tool(
        server_name="fs",
        session=_ErrorSession(),
        mcp_tool=_FakeMCPTool(
            name="write_file",
            description="Write file.",
            inputSchema={"type": "object", "properties": {}},
        ),
    )
    registry = ToolRegistry()
    registry.register(wrapped)

    res = await registry.execute(
        ToolCall(id="err-1", name="fs__write_file", arguments={})
    )
    assert res.is_error is True
    assert "permission denied" in res.output
