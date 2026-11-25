"""Config-driven HarnessBuilder wiring providers, tools, MCP, .agent/, memory, and tracing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from harness.core.agent import AgentLoop
from harness.core.context import ContextManager
from harness.core.hooks import HookManager
from harness.core.models import HarnessLimits, ModelConfig
from harness.loaders.agents_dir import AgentsDirLoader
from harness.memory.base import MemoryStore
from harness.memory.file_store import JSONFileMemoryStore
from harness.memory.in_memory import InMemoryStore
from harness.memory.reflector import ReflectorHook
from harness.observability.tracer import TraceLogger
from harness.providers.base import LLMProvider, ProviderRegistry
from harness.tools.mcp_bridge import MCPBridge, TransportFactory
from harness.tools.registry import ToolRegistry


class HarnessBuilder:
    """Assembles an AgentLoop from YAML config and plug-and-play components."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @classmethod
    def from_yaml(cls, path: str | Path) -> HarnessBuilder:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(config=raw)

    def _prepare_components(
        self,
        provider: LLMProvider | None,
        tool_registry: ToolRegistry | None,
        memory: MemoryStore | None,
        hooks: HookManager | None,
        base_system_prompt: str,
    ) -> tuple[AgentLoop, dict[str, Any]]:
        llm_cfg = ModelConfig(**(self.config.get("llm") or {}))
        limits = HarnessLimits(**(self.config.get("limits") or {}))
        mem_cfg = self.config.get("memory") or {}
        obs_cfg = self.config.get("observability") or {}

        resolved_provider = provider or ProviderRegistry.create(llm_cfg.provider, llm_cfg)
        registry = tool_registry or ToolRegistry()

        rules: list[str] = []
        skill_summaries: list[tuple[str, str]] = []
        mcp_servers: dict[str, Any] = dict(self.config.get("mcp_servers") or {})

        agents_dir = self.config.get("agents_dir")
        if agents_dir and Path(agents_dir).exists():
            loader = AgentsDirLoader(Path(agents_dir))
            bundle = loader.load()
            rules = bundle.always_on_rules
            skill_summaries = bundle.skill_summaries
            if bundle.skills:
                registry.register(loader.make_read_skill_tool(bundle.skills))
            mcp_servers.update(bundle.mcp_servers)

        offload_raw = mem_cfg.get("offload_dir")
        context_manager = ContextManager(
            base_system_prompt=base_system_prompt,
            rules=rules,
            skill_summaries=skill_summaries,
            short_term_window=int(mem_cfg.get("short_term_window", 20)),
            compaction_threshold_messages=int(
                mem_cfg.get("compaction_threshold_messages", 30)
            ),
            tool_output_max_chars=int(mem_cfg.get("tool_output_max_chars", 4000)),
            offload_dir=Path(offload_raw) if offload_raw else None,
        )

        tracer = None
        if obs_cfg.get("enable_tracing", True):
            tracer = TraceLogger(Path(obs_cfg.get("trace_dir", "traces")))

        if memory is not None:
            resolved_memory: MemoryStore = memory
        elif mem_cfg.get("persist_dir"):
            resolved_memory = JSONFileMemoryStore(Path(mem_cfg["persist_dir"]))
        else:
            resolved_memory = InMemoryStore()
        resolved_hooks = hooks or HookManager([ReflectorHook(resolved_memory)])

        agent = AgentLoop(
            provider=resolved_provider,
            tool_registry=registry,
            context_manager=context_manager,
            model_config=llm_cfg,
            limits=limits,
            hooks=resolved_hooks,
            memory=resolved_memory,
            tracer=tracer,
        )
        return agent, mcp_servers

    def build(
        self,
        provider: LLMProvider | None = None,
        tool_registry: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        hooks: HookManager | None = None,
        base_system_prompt: str = "",
    ) -> AgentLoop:
        """Build a synchronous AgentLoop (does not connect MCP servers)."""
        agent, _ = self._prepare_components(
            provider=provider,
            tool_registry=tool_registry,
            memory=memory,
            hooks=hooks,
            base_system_prompt=base_system_prompt,
        )
        return agent

    async def build_async(
        self,
        provider: LLMProvider | None = None,
        tool_registry: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        hooks: HookManager | None = None,
        base_system_prompt: str = "",
        mcp_session_factory: TransportFactory | None = None,
    ) -> AgentLoop:
        """Build an AgentLoop and open persistent MCP sessions if configured.

        Use `async with await builder.build_async() as agent:` or call
        `await agent.close()` when finished to clean up MCP subprocesses.
        """
        agent, mcp_servers = self._prepare_components(
            provider=provider,
            tool_registry=tool_registry,
            memory=memory,
            hooks=hooks,
            base_system_prompt=base_system_prompt,
        )
        if mcp_servers:
            bridge = MCPBridge(
                servers_config=mcp_servers,
                session_factory=mcp_session_factory,
            )
            try:
                await bridge.register_into(agent.tool_registry)
            except Exception:
                await bridge.close()
                raise
            agent.mcp_bridge = bridge
        return agent

    abuild = build_async
