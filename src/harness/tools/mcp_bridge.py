"""MCPBridge adapting Model Context Protocol servers into persistent Harness tools."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any, Protocol

from pydantic import BaseModel

from harness.core.models import ToolSpec
from harness.tools.decorator import RegisteredTool
from harness.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class _DynamicMCPArgs(BaseModel):
    model_config = {"extra": "allow"}


class MCPSession(Protocol):
    """Minimal protocol matching mcp.ClientSession for testability."""

    async def initialize(self) -> Any: ...

    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


TransportFactory = Callable[[AsyncExitStack, str, dict[str, Any]], Any]


async def _default_stdio_session_factory(
    stack: AsyncExitStack, server_name: str, cfg: dict[str, Any]
) -> MCPSession:
    """Open a persistent stdio MCP ClientSession inside the given AsyncExitStack."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise ImportError(
            "mcp package is required for MCPBridge. "
            "Install with: pip install 'harness-ai[mcp]'"
        ) from exc

    command = cfg.get("command")
    if not command:
        raise ValueError(f"MCP server '{server_name}' is missing 'command'.")

    params = StdioServerParameters(
        command=command,
        args=cfg.get("args", []),
        env=cfg.get("env"),
    )
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


class MCPBridge:
    """Manages persistent MCP sessions and registers their tools into ToolRegistry."""

    def __init__(
        self,
        servers_config: dict[str, Any] | None = None,
        session_factory: TransportFactory | None = None,
    ) -> None:
        self.servers_config = servers_config or {}
        self._session_factory = session_factory or _default_stdio_session_factory
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, MCPSession] = {}

    @property
    def is_connected(self) -> bool:
        return self._exit_stack is not None and bool(self._sessions)

    async def __aenter__(self) -> MCPBridge:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open persistent sessions for all configured MCP servers."""
        if not self.servers_config or self._exit_stack is not None:
            return

        stack = AsyncExitStack()
        try:
            for server_name, cfg in self.servers_config.items():
                if not cfg.get("command") and self._session_factory is _default_stdio_session_factory:
                    logger.warning(
                        "Skipping MCP server '%s': default stdio transport requires 'command'.",
                        server_name,
                    )
                    continue
                session = await self._session_factory(stack, server_name, cfg)
                self._sessions[server_name] = session
            self._exit_stack = stack
        except Exception:
            await stack.aclose()
            self._sessions.clear()
            raise

    async def close(self) -> None:
        """Close all persistent MCP sessions and clean up subprocesses."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            finally:
                self._exit_stack = None
                self._sessions.clear()

    aclose = close

    async def register_into(self, registry: ToolRegistry) -> int:
        """Connect (if needed), discover tools, and register them using persistent sessions."""
        if not self.servers_config:
            return 0

        if self._exit_stack is None:
            await self.connect()

        registered_count = 0
        for server_name, session in self._sessions.items():
            listed = await session.list_tools()
            for mcp_tool in listed.tools:
                wrapped = self.wrap_session_tool(
                    server_name=server_name,
                    session=session,
                    mcp_tool=mcp_tool,
                )
                registry.register(wrapped)
                registered_count += 1
        return registered_count

    @staticmethod
    def wrap_session_tool(
        server_name: str, session: MCPSession, mcp_tool: Any
    ) -> RegisteredTool:
        tool_name = f"{server_name}__{mcp_tool.name}"

        async def _caller(**kwargs: Any) -> str:
            res = await session.call_tool(mcp_tool.name, arguments=kwargs)
            parts = [getattr(block, "text", str(block)) for block in res.content]
            output = "\n".join(parts)
            if getattr(res, "isError", False):
                raise RuntimeError(
                    output or f"MCP tool '{mcp_tool.name}' returned an error."
                )
            return output

        spec = ToolSpec(
            name=tool_name,
            description=getattr(mcp_tool, "description", None)
            or f"MCP tool {mcp_tool.name}",
            parameters_schema=getattr(mcp_tool, "inputSchema", None)
            or {"type": "object", "properties": {}},
            requires_approval=False,
        )
        return RegisteredTool(
            spec=spec,
            fn=_caller,
            args_model=_DynamicMCPArgs,
            is_async=True,
        )
