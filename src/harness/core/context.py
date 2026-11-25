"""Context assembly, short-term sliding window/compaction, and output offloading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from harness.core.models import Message, ModelConfig, Role
from harness.core.state import SessionState
from harness.memory.base import EpisodeRecord

if TYPE_CHECKING:
    from harness.providers.base import LLMProvider


class ContextManager:
    """Builds model-ready message lists while respecting context limits."""

    def __init__(
        self,
        base_system_prompt: str = "",
        rules: list[str] | None = None,
        skill_summaries: list[tuple[str, str]] | None = None,
        short_term_window: int = 20,
        compaction_threshold_messages: int = 30,
        tool_output_max_chars: int = 4000,
        offload_dir: Path | None = None,
    ) -> None:
        self.base_system_prompt = base_system_prompt
        self.rules = rules or []
        self.skill_summaries = skill_summaries or []
        self.short_term_window = short_term_window
        self.compaction_threshold_messages = compaction_threshold_messages
        self.tool_output_max_chars = tool_output_max_chars
        self.offload_dir = offload_dir

    def build_system_prompt(self, episodes: list[EpisodeRecord] | None = None) -> str:
        sections: list[str] = []
        if self.base_system_prompt.strip():
            sections.append(self.base_system_prompt.strip())

        if self.rules:
            sections.append("## Project Rules\n" + "\n\n".join(self.rules))

        if self.skill_summaries:
            lines = [
                f"- `{name}`: {desc}" for name, desc in self.skill_summaries
            ]
            sections.append(
                "## Available Skills (call `read_skill` to load full instructions)\n"
                + "\n".join(lines)
            )

        if episodes:
            ep_lines = [
                f"- Context: {ep.context_summary} | Lesson: {ep.lesson}"
                for ep in episodes
            ]
            sections.append("## Past Lessons (Episodic Memory)\n" + "\n".join(ep_lines))

        return "\n\n".join(sections)

    def prepare_messages(
        self,
        state: SessionState,
        episodes: list[EpisodeRecord] | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> list[Message]:
        """Assemble system prompt + bounded short-term history."""
        system_prompt = self.build_system_prompt(episodes=episodes)
        if response_schema is not None:
            schema_json = json.dumps(response_schema.model_json_schema())
            schema_note = (
                "## Output Format\n"
                "Return ONLY valid JSON matching this schema:\n"
                f"{schema_json}"
            )
            system_prompt = (
                f"{system_prompt}\n\n{schema_note}" if system_prompt else schema_note
            )
        history = state.messages
        if len(history) > self.short_term_window:
            start_idx = len(history) - self.short_term_window
            # Never split an ASSISTANT(tool_calls) message from its TOOL responses
            while start_idx > 0 and history[start_idx].role == Role.TOOL:
                start_idx -= 1
            if start_idx > 0:
                summary_msg = Message(
                    role=Role.USER,
                    content=f"[Context compacted: {start_idx} earlier messages omitted for brevity.]",
                )
                history = [summary_msg, *history[start_idx:]]
            else:
                history = list(history)

        if system_prompt:
            return [Message(role=Role.SYSTEM, content=system_prompt), *history]
        return list(history)

    async def compact_if_needed(
        self,
        state: SessionState,
        provider: LLMProvider,
        config: ModelConfig,
    ) -> bool:
        """Summarize older messages using the LLM when exceeding compaction_threshold_messages."""
        if len(state.messages) <= self.compaction_threshold_messages:
            return False
        if len(state.messages) <= self.short_term_window:
            return False

        cutoff = len(state.messages) - self.short_term_window
        # Never split an ASSISTANT(tool_calls) message from its TOOL responses
        while cutoff > 0 and state.messages[cutoff].role == Role.TOOL:
            cutoff -= 1
        if cutoff == 0:
            return False

        old_messages = state.messages[:cutoff]
        recent_messages = state.messages[cutoff:]

        transcript_lines: list[str] = []
        for m in old_messages:
            parts: list[str] = []
            if m.content:
                parts.append(m.content)
            if m.tool_calls:
                calls_str = ", ".join(
                    f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True)})"
                    for tc in m.tool_calls
                )
                parts.append(f"[tool_calls: {calls_str}]")
            if parts:
                label = (
                    f"{m.role}({m.name})"
                    if m.role == Role.TOOL and m.name
                    else str(m.role)
                )
                transcript_lines.append(f"{label}: {' '.join(parts)}")
        transcript = "\n".join(transcript_lines)
        summary_prompt = [
            Message(
                role=Role.SYSTEM,
                content="Summarize the earlier conversation concisely, preserving key facts, decisions, and tool outcomes.",
            ),
            Message(role=Role.USER, content=transcript),
        ]
        summary_resp = await provider.generate(messages=summary_prompt, config=config)
        state.record_usage(
            summary_resp.usage.total_tokens, summary_resp.usage.cost_usd
        )
        summary_msg = Message(
            role=Role.USER,
            content=f"[Conversation summary]: {summary_resp.content}",
        )
        state.messages = [summary_msg, *recent_messages]
        return True

    def offload_tool_output(self, tool_call_id: str, output: str) -> str:
        """Truncate oversized tool output and optionally save full text to disk."""
        if len(output) <= self.tool_output_max_chars:
            return output

        note = f"\n...[truncated {len(output) - self.tool_output_max_chars} chars"
        if self.offload_dir is not None:
            self.offload_dir.mkdir(parents=True, exist_ok=True)
            path = self.offload_dir / f"{tool_call_id}.txt"
            path.write_text(output, encoding="utf-8")
            note += f"; full output saved to {path}]"
        else:
            note += "]"

        return output[: self.tool_output_max_chars] + note
