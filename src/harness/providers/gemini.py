"""Google Gemini adapter using the official google-genai SDK (optional dependency)."""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from harness.core.models import (
    Message,
    ModelConfig,
    ModelResponse,
    Role,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolSpec,
)
from harness.providers.base import ProviderRegistry
from harness.providers.pricing import estimate_cost_usd


class GeminiAdapter:
    """Adapter translating canonical Harness models to/from google-genai."""

    def __init__(
        self, api_key: str | None = None, default_model: str = "gemini-2.5-flash"
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for GeminiAdapter. "
                "Install with: pip install 'harness-ai[gemini]'"
            ) from exc

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self._client = genai.Client(api_key=key)
        self._default_model = default_model

    def _build_request(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        config: ModelConfig | None,
    ) -> tuple[str, list[dict[str, Any]], Any]:
        from google.genai import types

        cfg = config or ModelConfig(provider="gemini", model=self._default_model)
        system_parts = [
            m.content for m in messages if m.role == Role.SYSTEM and m.content
        ]
        system_instruction = "\n\n".join(system_parts) if system_parts else None

        contents: list[dict[str, Any]] = []
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            if msg.role == Role.SYSTEM:
                idx += 1
                continue
            if msg.role == Role.TOOL:
                tool_parts: list[dict[str, Any]] = []
                while idx < len(messages) and messages[idx].role == Role.TOOL:
                    tmsg = messages[idx]
                    tool_parts.append(
                        {
                            "function_response": {
                                "name": tmsg.name or "tool",
                                "response": {"result": tmsg.content},
                            }
                        }
                    )
                    idx += 1
                contents.append({"role": "user", "parts": tool_parts})
                continue

            role = "model" if msg.role == Role.ASSISTANT else "user"
            parts: list[dict[str, Any]] = []
            if msg.content:
                parts.append({"text": msg.content})
            for tc in msg.tool_calls:
                part_dict: dict[str, Any] = {
                    "function_call": {"name": tc.name, "args": tc.arguments}
                }
                if "thought_signature_b64" in tc.metadata:
                    part_dict["thought_signature"] = base64.b64decode(
                        tc.metadata["thought_signature_b64"]
                    )
                elif "thought_signature" in tc.metadata:
                    part_dict["thought_signature"] = tc.metadata["thought_signature"]
                parts.append(part_dict)
            if parts:
                contents.append({"role": role, "parts": parts})
            idx += 1

        genai_tools = None
        if tools:
            decls = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters_json_schema=t.parameters_schema,
                )
                for t in tools
            ]
            genai_tools = [types.Tool(function_declarations=decls)]

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "temperature": cfg.temperature,
            "max_output_tokens": cfg.max_tokens,
            "tools": genai_tools,
        }
        if cfg.response_schema is not None and genai_tools is None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = cfg.response_schema
        if cfg.provider_options:
            config_kwargs.update(cfg.provider_options)

        gen_config = types.GenerateContentConfig(**config_kwargs)
        return cfg.model or self._default_model, contents, gen_config

    @staticmethod
    def _extract_text(resp: Any) -> str:
        """Safely extract text parts without triggering warnings on function_call-only responses."""
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            texts = [
                part.text
                for part in parts
                if getattr(part, "text", None) and not getattr(part, "thought", False)
            ]
            return "".join(texts)
        return getattr(resp, "text", None) or ""

    @staticmethod
    def _extract_thinking(resp: Any) -> str:
        """Extract thought summary text parts when thinking is enabled."""
        candidates = getattr(resp, "candidates", None) or []
        if not candidates:
            return ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        thoughts = [
            part.text
            for part in parts
            if getattr(part, "text", None) and getattr(part, "thought", False)
        ]
        return "".join(thoughts)

    @staticmethod
    def _extract_tool_calls(resp: Any) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                fc = getattr(part, "function_call", None)
                if not fc:
                    continue
                metadata: dict[str, Any] = {}
                sig = getattr(part, "thought_signature", None)
                if isinstance(sig, bytes):
                    metadata["thought_signature_b64"] = base64.b64encode(sig).decode(
                        "ascii"
                    )
                elif sig is not None:
                    metadata["thought_signature"] = sig
                tool_calls.append(
                    ToolCall(
                        id=getattr(fc, "id", None) or uuid.uuid4().hex[:12],
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                        metadata=metadata,
                    )
                )
        if not tool_calls and getattr(resp, "function_calls", None):
            for fc in resp.function_calls:
                tool_calls.append(
                    ToolCall(
                        id=getattr(fc, "id", None) or uuid.uuid4().hex[:12],
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                    )
                )
        return tool_calls

    @staticmethod
    def _extract_usage(resp: Any, model_name: str = "") -> TokenUsage:
        meta = getattr(resp, "usage_metadata", None)
        if not meta:
            return TokenUsage()
        input_tokens = meta.prompt_token_count or 0
        output_tokens = meta.candidates_token_count or 0
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(model_name, input_tokens, output_tokens),
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> ModelResponse:
        model_name, contents, gen_config = self._build_request(messages, tools, config)
        resp = await self._client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=gen_config,
        )
        return ModelResponse(
            content=self._extract_text(resp),
            tool_calls=self._extract_tool_calls(resp),
            usage=self._extract_usage(resp, model_name=model_name),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: ModelConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        model_name, contents, gen_config = self._build_request(messages, tools, config)
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = TokenUsage()

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=model_name,
            contents=contents,
            config=gen_config,
        ):
            thinking = self._extract_thinking(chunk)
            delta = self._extract_text(chunk)
            if delta:
                text_parts.append(delta)
            if thinking or delta:
                yield StreamChunk(delta=delta, thinking=thinking)
            tool_calls.extend(self._extract_tool_calls(chunk))
            if getattr(chunk, "usage_metadata", None):
                usage = self._extract_usage(chunk, model_name=model_name)

        yield StreamChunk(
            response=ModelResponse(
                content="".join(text_parts),
                tool_calls=tool_calls,
                usage=usage,
            )
        )


ProviderRegistry.register(
    "gemini",
    lambda cfg: GeminiAdapter(default_model=cfg.model),
)
