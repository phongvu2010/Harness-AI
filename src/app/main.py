"""CLI entrypoint demonstrating HarnessBuilder.abuild and TokenDelta streaming."""

from __future__ import annotations

import asyncio
import sys

from app.tools import delete_record, get_project_status
from harness.core.builder import HarnessBuilder
from harness.tools.registry import ToolRegistry


async def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or "Check status for billing-api"

    registry = ToolRegistry()
    registry.register_many([get_project_status, delete_record])

    agent = await HarnessBuilder.from_yaml("configs/default.yaml").abuild(
        tool_registry=registry,
        base_system_prompt="You are a helpful AI assistant powered by Harness AI.",
    )

    async with agent:
        print("Assistant: ", end="", flush=True)
        streamed = False
        async for event in agent.stream(user_input=prompt):
            if event.type == "token_delta":
                print(event.content, end="", flush=True)
                streamed = True
            elif event.type == "tool_start" and event.tool_call:
                print(
                    f"\n[tool_start] {event.tool_call.name}({event.tool_call.arguments})"
                )
            elif event.type == "tool_end" and event.tool_result:
                print(f"[tool_end] {event.tool_result.output}")
            elif event.type == "final_answer":
                if not streamed:
                    print(event.content, end="")
                print()
            elif event.type == "error":
                print(f"\nError: {event.content}")


if __name__ == "__main__":
    asyncio.run(main())
