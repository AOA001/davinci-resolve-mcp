"""FastMCP stdio helpers with strict LF line endings across platforms."""

from __future__ import annotations

import sys
from io import TextIOWrapper

import anyio


def create_text_stdio():
    """Wrap stdio in UTF-8 text mode without platform newline translation."""
    stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline=""))
    stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline=""))
    return stdin, stdout


async def run_fastmcp_stdio_async(fastmcp):
    """Run a FastMCP server over stdio using strict LF-delimited JSON."""
    from src.utils.mcp_sdk import run_stdio_async

    stdin, stdout = create_text_stdio()
    await run_stdio_async(fastmcp, stdin=stdin, stdout=stdout)


def run_fastmcp_stdio(fastmcp):
    """Synchronous entrypoint for strict-LF FastMCP stdio."""
    anyio.run(lambda: run_fastmcp_stdio_async(fastmcp))
