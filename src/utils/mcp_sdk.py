"""One place that knows which MCP server SDK is installed.

The server was written against the official SDK's v1 ``mcp.server.fastmcp``.
That module is gone in ``mcp`` 2.x, and the Skills over MCP extension
(``io.modelcontextprotocol/skills``) ships in standalone FastMCP 4.2+, which
sits on ``mcp`` 2.x. Every module that used to import the SDK directly imports
from here instead, so the rest of the code does not care which one runs.

Backends, tried in order:

* ``fastmcp4`` - standalone ``fastmcp`` >= 4.2 (preferred; serves skills).
* ``mcp1``     - the official SDK's v1 ``mcp.server.fastmcp`` (fallback, so an
  older venv keeps working exactly as before).

``BACKEND`` names the one in use. Set ``DAVINCI_MCP_SDK=mcp1`` to force the old
SDK when both are installed.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Iterator, Optional

BACKEND: str = ""
_forced = os.environ.get("DAVINCI_MCP_SDK", "").strip().lower()

if _forced != "mcp1":
    try:
        import fastmcp as _fastmcp_pkg
        from fastmcp import Context, FastMCP as _BaseFastMCP
        from fastmcp.utilities.types import Image
        from mcp import types as mcp_types
        from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

        _major = int(str(getattr(_fastmcp_pkg, "__version__", "0")).split(".")[0] or 0)
        if _major < 4:
            raise ImportError(f"fastmcp {_fastmcp_pkg.__version__} is older than 4")
        BACKEND = "fastmcp4"
    except Exception:  # pragma: no cover - depends on the installed SDK
        BACKEND = ""

if not BACKEND:
    from mcp import types as mcp_types  # type: ignore[no-redef]
    from mcp.server.fastmcp import Context, FastMCP as _BaseFastMCP, Image  # type: ignore[no-redef]
    from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations  # type: ignore[no-redef]

    BACKEND = "mcp1"

IS_FASTMCP4 = BACKEND == "fastmcp4"

__all__ = [
    "BACKEND",
    "IS_FASTMCP4",
    "CallToolResult",
    "Context",
    "FastMCP",
    "Image",
    "ImageContent",
    "TextContent",
    "ToolAnnotations",
    "mcp_types",
    "add_skill_directories",
    "get_tool",
    "iter_tools",
    "list_wire_tools",
    "result_content",
    "result_structured",
    "block_mime_type",
    "is_error_result",
    "run_stdio_async",
    "set_server_version",
]


if IS_FASTMCP4:
    from fastmcp.utilities.types import NotSet as _NotSet

    class FastMCP(_BaseFastMCP):  # type: ignore[no-redef,misc]
        """FastMCP 4 that still accepts the v1 ``structured_output=`` keyword."""

        def tool(self, name_or_fn: Any = None, *args: Any, structured_output: Optional[bool] = None,
                 **kwargs: Any) -> Any:
            if structured_output is False and "output_schema" not in kwargs:
                kwargs["output_schema"] = None
            elif structured_output is not None and "output_schema" not in kwargs:
                kwargs["output_schema"] = _NotSet
            return super().tool(name_or_fn, *args, **kwargs)
else:
    FastMCP = _BaseFastMCP  # type: ignore[misc]


def set_server_version(server: Any, version: str) -> None:
    """Report `version` in the initialize handshake's serverInfo."""
    low = getattr(server, "_mcp_server", None)
    if low is not None:
        try:
            low.version = version
        except Exception:
            pass


def iter_tools(server: Any) -> Iterator[Any]:
    """Every registered tool object, without going through the event loop."""
    if IS_FASTMCP4:
        provider = getattr(server, "local_provider", None) or getattr(server, "_local_provider", None)
        components = getattr(provider, "_components", None) or {}
        values: Iterable[Any] = components.values() if isinstance(components, dict) else components
        for component in values:
            if hasattr(component, "fn") and hasattr(component, "parameters") and hasattr(component, "run"):
                yield component
        return
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if isinstance(tools, dict):
        yield from tools.values()


def get_tool(server: Any, name: str) -> Any:
    """The registered tool called `name`, or None."""
    for tool in iter_tools(server):
        if getattr(tool, "name", None) == name:
            return tool
    if not IS_FASTMCP4:
        try:
            return server._tool_manager.get_tool(name)
        except Exception:
            return None
    return None


def is_error_result(result: Any) -> bool:
    """`isError` of a CallToolResult under either SDK's field naming."""
    for attr in ("is_error", "isError"):
        value = getattr(result, attr, None)
        if value is not None:
            return bool(value)
    return False


def add_skill_directories(server: Any, roots: Iterable[str], logger: Any = None) -> int:
    """Serve each folder of SKILL.md skills through Skills over MCP.

    Only FastMCP 4.2+ can do this; on the v1 SDK it is a no-op. A root that is
    missing, or whose skills break the spec, is skipped with a warning rather
    than stopping the server. Returns the number of roots added.
    """
    if not IS_FASTMCP4:
        return 0
    try:
        from fastmcp.skills.provider import SkillsDirectoryProvider
    except Exception as exc:  # pragma: no cover - older fastmcp 4.x
        if logger:
            logger.warning(f"Skills over MCP unavailable in this fastmcp: {exc}")
        return 0
    added = 0
    for root in roots:
        root = os.path.expanduser(str(root or "").strip())
        if not root or not os.path.isdir(root):
            continue
        try:
            server.add_provider(SkillsDirectoryProvider(root, reload=True))
            added += 1
        except Exception as exc:
            if logger:
                logger.warning(f"Skipped skills folder {root}: {exc}")
    return added


async def run_stdio_async(server: Any, stdin: Any = None, stdout: Any = None) -> None:
    """Run `server` over stdio, optionally on caller-supplied text streams."""
    if IS_FASTMCP4:
        from mcp.server.stdio import stdio_server
        from fastmcp.server.context import reset_transport, set_transport

        token = set_transport("stdio")
        try:
            async with server._lifespan_manager():  # noqa: SLF001 - mirrors FastMCP.run_stdio_async
                async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
                    await server._mcp_server.run(
                        read_stream,
                        write_stream,
                        server._mcp_server.create_initialization_options(),
                    )
        finally:
            reset_transport(token)
        return
    from mcp.server.stdio import stdio_server

    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        await server._mcp_server.run(  # noqa: SLF001 - no public injection point exists yet
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),
        )


async def list_wire_tools(server: Any) -> list:
    """`tools/list` as the client sees it: MCP `Tool` objects with inputSchema.

    v1's `FastMCP.list_tools()` already returns these; FastMCP 4's returns its
    own component objects, so convert them.
    """
    tools = await server.list_tools()
    if not IS_FASTMCP4:
        return list(tools)
    return [t.to_mcp_tool() if hasattr(t, "to_mcp_tool") else t for t in tools]


def result_content(result: Any) -> list:
    """The content blocks of a `call_tool` result, whatever shape the SDK used."""
    if isinstance(result, tuple) and len(result) == 2:
        return list(result[0])
    content = getattr(result, "content", None)
    if content is not None:
        return list(content)
    return list(result)


def result_structured(result: Any) -> Any:
    """The structured content of a `call_tool` result, or None."""
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    missing = object()
    for attr in ("structured_content", "structuredContent"):
        value = getattr(result, attr, missing)
        if value is not missing:
            return value
    return None


def block_mime_type(block: Any) -> Any:
    """A content block's MIME type under either SDK's field naming."""
    return getattr(block, "mime_type", None) or getattr(block, "mimeType", None)
