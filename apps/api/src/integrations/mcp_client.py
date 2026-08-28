import json
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters, stdio_client
from pydantic import ValidationError

from domain.investigation import ToolErrorCode, ToolResult
from integrations.mcp_tools import MCP_TOOL_NAMES
from services.tool_registry import InvestigationToolRegistry
from tools.common import failure


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class MCPToolTransportError(RuntimeError):
    """Controlled infrastructure error that does not expose MCP internals."""


class MCPInvestigationToolRegistry(InvestigationToolRegistry):
    transport = "mcp"

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        python_executable: str | None = None,
        server_module: str = "integrations.mcp_server",
    ) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds
        self._python_executable = python_executable or sys.executable
        self._server_module = server_module

    @property
    def names(self) -> tuple[str, ...]:
        return MCP_TOOL_NAMES

    @property
    def openai_tools(self) -> list[dict[str, Any]]:
        return self.openai_tools_for(MCP_TOOL_NAMES)

    def dispatch(self, name: str, raw_arguments: str) -> tuple[dict[str, Any], ToolResult]:
        if name not in MCP_TOOL_NAMES:
            return {}, failure(
                name,
                "unknown",
                ToolErrorCode.UNKNOWN_TOOL,
                f"Unknown tool '{name}'",
            )
        arguments, error = self.validate_arguments(name, raw_arguments)
        if error is not None:
            return arguments, error
        return arguments, self.call_tool(name, arguments)

    def execute(self, name: str, arguments: dict[str, str]) -> ToolResult:
        if name not in MCP_TOOL_NAMES:
            return failure(
                name,
                "unknown",
                ToolErrorCode.UNKNOWN_TOOL,
                f"Unknown tool '{name}'",
            )
        return self.call_tool(name, arguments)

    def list_tools(self) -> tuple[str, ...]:
        return tuple(self._run(self._list_tools()))

    def call_tool(self, name: str, arguments: dict[str, str]) -> ToolResult:
        if name not in MCP_TOOL_NAMES:
            return failure(
                name,
                "unknown",
                ToolErrorCode.UNKNOWN_TOOL,
                f"Unknown tool '{name}'",
            )
        validated, error = self.validate_arguments(name, json.dumps(arguments))
        if error is not None:
            return error
        return self._run(self._call_tool(name, validated))

    def _run(self, operation):
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(anyio.run, lambda: operation)
                return future.result(timeout=self._timeout_seconds + 2)
        except (FutureTimeoutError, TimeoutError) as error:
            raise MCPToolTransportError("MCP tool execution timed out") from error
        except MCPToolTransportError:
            raise
        except Exception as error:
            raise MCPToolTransportError("MCP tool execution failed") from error

    def _server_parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self._python_executable,
            args=["-m", self._server_module],
            env={"PYTHONPATH": str(SOURCE_ROOT)},
        )

    async def _list_tools(self) -> list[str]:
        with anyio.fail_after(self._timeout_seconds):
            async with Client(stdio_client(self._server_parameters())) as client:
                result = await client.list_tools()
                return [tool.name for tool in result.tools]

    async def _call_tool(
        self, name: str, arguments: dict[str, str]
    ) -> ToolResult:
        with anyio.fail_after(self._timeout_seconds):
            async with Client(stdio_client(self._server_parameters())) as client:
                result = await client.call_tool(name, arguments)
        if result.is_error or result.structured_content is None:
            raise MCPToolTransportError("MCP tool returned a controlled failure")
        try:
            return ToolResult.model_validate(result.structured_content)
        except ValidationError as error:
            raise MCPToolTransportError("MCP tool returned an invalid result") from error


def build_investigation_tools(settings) -> InvestigationToolRegistry:
    if settings.tool_transport == "mcp":
        return MCPInvestigationToolRegistry(
            timeout_seconds=settings.mcp_timeout_seconds
        )
    return InvestigationToolRegistry()
