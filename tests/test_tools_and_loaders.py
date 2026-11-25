"""Tests for @tool schema generation, ToolRegistry validation, AgentsDirLoader, and MCPBridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from harness.core.models import ToolCall
from harness.loaders.agents_dir import AgentsDirLoader
from harness.tools.decorator import tool
from harness.tools.mcp_bridge import MCPBridge
from harness.tools.registry import ToolRegistry


async def test_tool_registry_validates_arguments() -> None:
    @tool(description="Multiply.")
    def multiply(a: int, b: int = 2) -> int:
        return a * b

    registry = ToolRegistry()
    registry.register(multiply)

    ok_res = await registry.execute(
        ToolCall(id="1", name="multiply", arguments={"a": 5})
    )
    assert ok_res.is_error is False
    assert ok_res.output == "10"

    bad_res = await registry.execute(
        ToolCall(id="2", name="multiply", arguments={"a": "not-an-int"})
    )
    assert bad_res.is_error is True
    assert "validation error" in bad_res.output.lower()


async def test_agents_dir_loader_progressive_disclosure(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "always.md").write_text(
        "---\ntrigger: always_on\n---\nAlways be concise.", encoding="utf-8"
    )
    (rules_dir / "conditional.md").write_text(
        "---\ntrigger: model_decision\n---\nOnly when asked.", encoding="utf-8"
    )

    skill_dir = tmp_path / "skills" / "deploy-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy-skill\ndescription: Deploy safely.\n---\nStep 1: Run tests.",
        encoding="utf-8",
    )

    loader = AgentsDirLoader(tmp_path)
    bundle = loader.load()

    assert bundle.always_on_rules == ["Always be concise."]
    assert bundle.skill_summaries == [("deploy-skill", "Deploy safely.")]

    read_skill_tool = loader.make_read_skill_tool(bundle.skills)
    registry = ToolRegistry()
    registry.register(read_skill_tool)

    res = await registry.execute(
        ToolCall(id="s1", name="read_skill", arguments={"skill_name": "deploy-skill"})
    )
    assert res.is_error is False
    assert "Step 1: Run tests." in res.output


async def test_mcp_bridge_wrap_session_tool() -> None:
    class FakeSession:
        async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
            return SimpleNamespace(
                content=[SimpleNamespace(text=f"called {name} with {arguments}")]
            )

    mcp_tool = SimpleNamespace(
        name="list_tables",
        description="List DB tables",
        inputSchema={"type": "object", "properties": {"schema": {"type": "string"}}},
    )
    wrapped = MCPBridge.wrap_session_tool("postgres", FakeSession(), mcp_tool)
    registry = ToolRegistry()
    registry.register(wrapped)

    res = await registry.execute(
        ToolCall(
            id="m1",
            name="postgres__list_tables",
            arguments={"schema": "public"},
        )
    )
    assert res.is_error is False
    assert "called list_tables" in res.output


async def test_sync_tool_runs_in_worker_thread() -> None:
    import threading

    main_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []

    @tool(description="Check thread.")
    def check_thread() -> str:
        worker_thread_ids.append(threading.get_ident())
        return "ok"

    registry = ToolRegistry()
    registry.register(check_thread)
    res = await registry.execute(ToolCall(id="t1", name="check_thread", arguments={}))

    assert res.is_error is False
    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != main_thread_id


async def test_harness_builder_abuild_wires_mcp_bridge(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from harness.core.builder import HarnessBuilder
    from harness.tools.mcp_bridge import MCPBridge

    (tmp_path / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo", "args": []}}}),
        encoding="utf-8",
    )

    called_with: list[dict] = []

    async def fake_register_into(self: MCPBridge, registry: ToolRegistry) -> int:
        called_with.append(self.servers_config)

        @tool(name="demo__hello", description="MCP hello")
        def demo_hello() -> str:
            return "hello from mcp"

        registry.register(demo_hello)
        return 1

    monkeypatch.setattr(MCPBridge, "register_into", fake_register_into)

    builder = HarnessBuilder(
        config={
            "llm": {"provider": "mock"},
            "agents_dir": str(tmp_path),
            "observability": {"enable_tracing": False},
        }
    )
    agent = await builder.abuild()

    assert len(called_with) == 1
    assert "demo" in called_with[0]
    assert agent.mcp_bridge is not None
    assert any(spec.name == "demo__hello" for spec in agent.tool_registry.list_specs())


def test_provider_registry_auto_loads_builtins() -> None:
    from harness.providers.base import ProviderRegistry

    providers = ProviderRegistry.list_providers()
    assert "mock" in providers
    assert "gemini" in providers


async def test_harness_builder_build_async_and_compaction_config() -> None:
    from harness.core.builder import HarnessBuilder

    builder = HarnessBuilder(
        config={
            "llm": {"provider": "mock"},
            "memory": {"compaction_threshold_messages": 42},
            "observability": {"enable_tracing": False},
        }
    )
    agent = await builder.build_async()
    assert agent.context_manager.compaction_threshold_messages == 42


def test_tool_decorator_preserves_direct_call() -> None:
    @tool(description="Add two numbers.")
    def add_nums(a: int, b: int = 1) -> int:
        return a + b

    assert add_nums(3, 4) == 7
    assert add_nums(5) == 6
