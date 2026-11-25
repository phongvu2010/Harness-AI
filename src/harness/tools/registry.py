"""Central ToolRegistry for local @tool functions and MCP-wrapped tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import ValidationError

from harness.core.models import ToolCall, ToolResult, ToolSpec
from harness.tools.decorator import RegisteredTool


class ToolRegistry:
    """Stores, validates, and executes tools uniformly."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, registered: RegisteredTool) -> None:
        self._tools[registered.spec.name] = registered

    def register_many(self, tools: list[RegisteredTool]) -> None:
        for item in tools:
            self.register(item)

    def list_specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def requires_approval(self, name: str) -> bool:
        item = self._tools.get(name)
        return item.spec.requires_approval if item else False

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        item = self._tools.get(tool_call.name)
        if item is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=f"Error: Unknown tool '{tool_call.name}'.",
                is_error=True,
            )

        try:
            validated = item.args_model.model_validate(tool_call.arguments)
            kwargs = validated.model_dump()
        except ValidationError as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=f"Argument validation error: {exc}",
                is_error=True,
            )

        try:
            raw_output: Any
            if item.is_async:
                raw_output = await item.fn(**kwargs)
            else:
                raw_output = await asyncio.to_thread(item.fn, **kwargs)

            if isinstance(raw_output, str):
                text_output = raw_output
            else:
                text_output = json.dumps(raw_output, ensure_ascii=False, default=str)

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=text_output,
                is_error=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=f"Tool execution error: {exc}",
                is_error=True,
            )
