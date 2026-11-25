"""Model token pricing registry for cost estimation and budget circuit breaking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """USD cost per 1M input and output tokens."""

    input_per_1m: float
    output_per_1m: float


# Default USD per 1M tokens (prefix-matched longest-first)
_PRICING_TABLE: dict[str, ModelPricing] = {
    "gemini-2.5-pro": ModelPricing(input_per_1m=1.25, output_per_1m=10.00),
    "gemini-2.5-flash-lite": ModelPricing(input_per_1m=0.075, output_per_1m=0.30),
    "gemini-2.5-flash": ModelPricing(input_per_1m=0.15, output_per_1m=0.60),
    "gemini-2.0-flash": ModelPricing(input_per_1m=0.10, output_per_1m=0.40),
    "gemini-1.5-pro": ModelPricing(input_per_1m=1.25, output_per_1m=5.00),
    "gemini-1.5-flash": ModelPricing(input_per_1m=0.075, output_per_1m=0.30),
}


def register_model_pricing(
    model_prefix: str, input_per_1m: float, output_per_1m: float
) -> None:
    """Register or override pricing for a model name or prefix."""
    _PRICING_TABLE[model_prefix.lower()] = ModelPricing(
        input_per_1m=input_per_1m,
        output_per_1m=output_per_1m,
    )


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate USD cost for a model call using longest-prefix matching."""
    normalized = model.lower()
    for prefix in sorted(_PRICING_TABLE, key=len, reverse=True):
        if normalized.startswith(prefix):
            pricing = _PRICING_TABLE[prefix]
            return (
                (input_tokens / 1_000_000) * pricing.input_per_1m
                + (output_tokens / 1_000_000) * pricing.output_per_1m
            )
    return 0.0
