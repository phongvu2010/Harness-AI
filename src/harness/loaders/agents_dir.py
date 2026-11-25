"""Direct loader for Antigravity 2.0 .agent/ directory (rules, skills, mcp_config.json)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harness.tools.decorator import RegisteredTool, tool

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class SkillEntry:
    """Metadata and lazy-loaded path for an Antigravity SKILL.md."""

    name: str
    description: str
    path: Path


@dataclass
class AgentsBundle:
    """Loaded rules, skill summaries, and MCP config from .agent/."""

    always_on_rules: list[str] = field(default_factory=list)
    skills: dict[str, SkillEntry] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)

    @property
    def skill_summaries(self) -> list[tuple[str, str]]:
        return [(s.name, s.description) for s in self.skills.values()]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    return meta, body


class AgentsDirLoader:
    """Reads .agent/ without conversion, supporting Progressive Disclosure for skills."""

    def __init__(self, agents_dir: Path | str = ".agent") -> None:
        self.agents_dir = Path(agents_dir)

    def load(self) -> AgentsBundle:
        bundle = AgentsBundle()
        if not self.agents_dir.exists():
            return bundle

        # 1. Rules (.agent/rules/*.md)
        rules_dir = self.agents_dir / "rules"
        if rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md")):
                meta, body = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                trigger = meta.get("trigger", "always_on")
                if trigger == "always_on" and body.strip():
                    bundle.always_on_rules.append(body.strip())

        # 2. Skills (.agent/skills/<name>/SKILL.md) - frontmatter only!
        skills_dir = self.agents_dir / "skills"
        if skills_dir.is_dir():
            for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
                meta, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                name = str(meta.get("name") or skill_md.parent.name)
                desc = str(meta.get("description") or "").strip()
                bundle.skills[name] = SkillEntry(
                    name=name,
                    description=desc,
                    path=skill_md,
                )

        # 3. MCP Config (.agent/mcp_config.json)
        mcp_file = self.agents_dir / "mcp_config.json"
        if mcp_file.is_file():
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
            bundle.mcp_servers = data.get("mcpServers", {})

        return bundle

    @staticmethod
    def make_read_skill_tool(skills: dict[str, SkillEntry]) -> RegisteredTool:
        """Create a sandboxed read_skill tool for Progressive Disclosure."""

        @tool(
            name="read_skill",
            description=(
                "Load full instructions (SKILL.md) or a supporting file inside "
                "an available skill directory by name."
            ),
        )
        def read_skill(skill_name: str, file_path: str | None = None) -> str:
            entry = skills.get(skill_name)
            if entry is None:
                available = ", ".join(sorted(skills)) or "(none)"
                return f"Skill '{skill_name}' not found. Available: {available}"
            if not file_path:
                return entry.path.read_text(encoding="utf-8")

            skill_root = entry.path.parent.resolve()
            target = (skill_root / file_path).resolve()
            if not target.is_relative_to(skill_root):
                return f"Access denied: '{file_path}' escapes skill '{skill_name}'."
            if not target.is_file():
                return f"File '{file_path}' not found in skill '{skill_name}'."
            return target.read_text(encoding="utf-8")

        return read_skill
