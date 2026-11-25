"""Domain exceptions for Harness AI."""


class HarnessError(Exception):
    """Base exception for all harness errors."""


class BudgetExceededError(HarnessError):
    """Raised when execution exceeds max_steps, max_tokens, or max_cost_usd."""


class LoopDetectedError(HarnessError):
    """Raised when the agent repeats identical tool calls consecutively."""


class StructuredOutputError(HarnessError):
    """Raised when model output fails Pydantic validation after retries."""
