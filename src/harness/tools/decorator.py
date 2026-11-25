"""@tool decorator generating Pydantic JSON Schemas from Python type hints."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, Field, create_model

from harness.core.models import ToolSpec


@dataclass
class RegisteredTool:
    """Wrapper binding a Python callable to its ToolSpec and validator model."""

    spec: ToolSpec
    fn: Callable[..., Any]
    args_model: type[BaseModel]
    is_async: bool

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Allow calling the decorated Python function directly."""
        return self.fn(*args, **kwargs)


def _build_args_model(fn: Callable[..., Any], tool_name: str) -> type[BaseModel]:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    fields: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, Any)
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, Field(...))
        else:
            fields[param_name] = (annotation, Field(default=param.default))

    return create_model(f"{tool_name}_Args", **fields)


def tool(
    name: str | None = None,
    description: str | None = None,
    requires_approval: bool = False,
) -> Callable[[Callable[..., Any]], RegisteredTool]:
    """Decorate a sync or async Python function as an agent tool."""

    def decorator(fn: Callable[..., Any]) -> RegisteredTool:
        tool_name = name or fn.__name__
        doc = (description or inspect.getdoc(fn) or "").strip()
        if not doc:
            doc = f"Execute {tool_name}."

        args_model = _build_args_model(fn, tool_name)
        spec = ToolSpec(
            name=tool_name,
            description=doc,
            parameters_schema=args_model.model_json_schema(),
            requires_approval=requires_approval,
        )
        return RegisteredTool(
            spec=spec,
            fn=fn,
            args_model=args_model,
            is_async=inspect.iscoroutinefunction(fn),
        )

    return decorator
