"""JSONL trace logger capturing run snapshots for debugging and replay."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.core.models import Message, ModelResponse


class TraceLogger:
    """Writes per-run execution traces to <trace_dir>/<run_id>.jsonl."""

    def __init__(self, trace_dir: Path | str = "traces") -> None:
        self.trace_dir = Path(trace_dir)

    def log_step(
        self,
        run_id: str,
        step: int,
        messages: list[Message],
        response: ModelResponse,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{run_id}.jsonl"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "step": step,
            "messages": [m.model_dump(mode="json") for m in messages],
            "response": response.model_dump(mode="json"),
            "extra": extra or {},
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
