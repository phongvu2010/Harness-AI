"""Offline eval runner executing JSONL datasets concurrently against HarnessBuilder."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from evals.scorers import contains_substring

from app.tools import delete_record, get_project_status
from harness.core.builder import HarnessBuilder
from harness.tools.registry import ToolRegistry


def _default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many([get_project_status, delete_record])
    return registry


async def run_eval(
    dataset_path: str | Path = "evals/datasets/sample.jsonl",
    config_path: str | Path = "configs/default.yaml",
    registry_factory: Callable[[], ToolRegistry] | None = None,
    max_concurrency: int = 4,
) -> tuple[int, int]:
    lines = Path(dataset_path).read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    make_registry = registry_factory or _default_registry
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    builder = HarnessBuilder.from_yaml(config_path)

    async def _run_case(case: dict[str, str]) -> tuple[str, bool, str]:
        async with semaphore:
            async with await builder.abuild(tool_registry=make_registry()) as agent:
                event = await agent.run(user_input=case["input"])
            ok = contains_substring(event, case["expected_substring"])
            return case["id"], ok, event.content

    results = await asyncio.gather(*(_run_case(case) for case in cases))
    passed = 0
    for case_id, ok, content in results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case_id}: {content}")
        if ok:
            passed += 1

    print(f"\nEval Summary: {passed}/{len(cases)} passed.")
    return passed, len(cases)


if __name__ == "__main__":
    passed, total = asyncio.run(run_eval())
    raise SystemExit(0 if passed == total else 1)
