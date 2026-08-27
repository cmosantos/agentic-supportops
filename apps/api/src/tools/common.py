from typing import Any

from domain.investigation import ToolError, ToolErrorCode, ToolResult


def success(tool: str, resource: str, data: dict[str, Any]) -> ToolResult:
    return ToolResult(tool=tool, resource=resource, success=True, data=data)


def failure(
    tool: str, resource: str, code: ToolErrorCode, message: str
) -> ToolResult:
    return ToolResult(
        tool=tool,
        resource=resource,
        success=False,
        error=ToolError(code=code, message=message),
    )

