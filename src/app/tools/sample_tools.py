"""Sample domain tools demonstrating @tool and requires_approval."""

from harness.tools.decorator import tool


@tool(description="Return sample health status for a named service.")
def get_project_status(service_name: str) -> dict[str, str]:
    return {"service": service_name, "status": "healthy", "version": "0.1.0"}


@tool(
    description="Delete a record by ID (requires human approval).",
    requires_approval=True,
)
def delete_record(record_id: str) -> dict[str, str]:
    return {"deleted": record_id, "status": "ok"}
