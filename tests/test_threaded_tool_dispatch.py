"""Sync tool bodies are moved off the event loop into a worker thread so a
blocking call cannot freeze the transport."""
import threading
import unittest

import anyio

from src.server import _bridge_lock, _install_threaded_tool_dispatch
from src.utils.mcp_sdk import IS_FASTMCP4


class FakeTool:
    def __init__(self, fn, is_async=False, name="t"):
        self.fn = fn
        self.is_async = is_async
        self.name = name


class FakeManager:
    def __init__(self, tools):
        self._tools = tools


class FakeMCP:
    def __init__(self, tools):
        self._tool_manager = FakeManager(tools)


@unittest.skipIf(IS_FASTMCP4, "drives the v1 SDK's ToolManager internals")
class ThreadedToolDispatchTest(unittest.TestCase):
    def test_wraps_sync_tools_and_skips_async(self):
        sync_tool = FakeTool(lambda **kw: {"ok": True}, is_async=False, name="sync")
        async_fn = sync_tool  # placeholder
        async_tool = FakeTool(async_fn, is_async=True, name="async")
        mcp = FakeMCP({"sync": sync_tool, "async": async_tool})

        wrapped = _install_threaded_tool_dispatch(mcp)
        self.assertEqual(wrapped, 1)
        self.assertTrue(sync_tool.is_async)   # now presented as async
        self.assertTrue(async_tool.is_async)  # untouched

    def test_wrapped_tool_runs_off_thread_and_returns_result(self):
        main_thread = threading.current_thread().name

        def body(**kwargs):
            return {"value": kwargs["x"], "thread": threading.current_thread().name}

        tool = FakeTool(body, is_async=False, name="body")
        _install_threaded_tool_dispatch(FakeMCP({"body": tool}))

        result = anyio.run(lambda: tool.fn(x=42))
        self.assertEqual(result["value"], 42)
        self.assertNotEqual(result["thread"], main_thread)  # ran off the event-loop thread

    def test_cancel_during_body_does_not_kill_the_session(self):
        # #272: the client cancels while the body is still in its worker
        # thread, and the body finishes afterwards. run_sync shields its wait,
        # so without a cancellation checkpoint the result came back normally,
        # the SDK answered the request a second time, and its "Request already
        # responded to" assert took the whole session down. Driven through the
        # real SDK over the in-memory transport, not the fakes above.
        from mcp import types
        from src.utils.mcp_sdk import FastMCP
        from mcp.shared.exceptions import McpError
        from mcp.shared.memory import create_connected_server_and_client_session

        app = FastMCP("cancel-probe")
        release = threading.Event()

        @app.tool()
        def slow() -> str:
            release.wait(5)
            return "slow done"

        @app.tool()
        def quick() -> str:
            return "quick done"

        _install_threaded_tool_dispatch(app)
        outcome = {}

        async def scenario():
            async with create_connected_server_and_client_session(
                app._mcp_server, raise_exceptions=False
            ) as client:
                async with anyio.create_task_group() as tg:
                    async def call_slow():
                        try:
                            await client.call_tool("slow", {})
                            outcome["slow"] = "returned"
                        except McpError as exc:
                            outcome["slow"] = exc.error.message

                    tg.start_soon(call_slow)
                    await anyio.sleep(0.3)
                    # id 0 was initialize, so the slow call is request 1.
                    await client.send_notification(types.ClientNotification(
                        types.CancelledNotification(params=types.CancelledNotificationParams(
                            requestId=1, reason="client timeout"))))
                    await anyio.sleep(0.2)
                    release.set()  # the body finishes AFTER the cancellation
                with anyio.fail_after(5):
                    result = await client.call_tool("quick", {})
                outcome["quick"] = result.content[0].text

        try:
            anyio.run(scenario)
        finally:
            release.set()
        self.assertEqual(outcome.get("slow"), "Request cancelled")
        self.assertEqual(outcome.get("quick"), "quick done")  # session still serving

    def test_missing_tool_manager_is_a_noop(self):
        self.assertEqual(_install_threaded_tool_dispatch(object()), 0)

    def test_non_mapping_tools_is_a_noop(self):
        # A future SDK shape that isn't a dict must degrade to inline, not crash.
        self.assertEqual(_install_threaded_tool_dispatch(FakeMCP(["not", "a", "map"])), 0)


@unittest.skipUnless(IS_FASTMCP4, "FastMCP 4 dispatch")
class BridgeLockedDispatchTest(unittest.TestCase):
    """FastMCP 4 threads sync tools itself; the bridge lock must still serialize them."""

    def _server(self):
        from src.utils.mcp_sdk import FastMCP

        app = FastMCP("lock-test")
        seen = {}

        @app.tool()
        def body(x: int) -> dict:
            seen["locked"] = _bridge_lock.locked()
            seen["thread"] = threading.current_thread().name
            return {"value": x}

        @app.tool()
        async def asynchronous(x: int) -> dict:
            return {"value": x}

        return app, seen

    def test_sync_tools_run_off_loop_holding_the_bridge_lock(self):
        app, seen = self._server()
        self.assertEqual(_install_threaded_tool_dispatch(app), 1)
        result = anyio.run(lambda: app.call_tool("body", {"x": 42}))
        self.assertEqual(result.structured_content["value"], 42)
        self.assertTrue(seen["locked"])
        self.assertNotEqual(seen["thread"], threading.current_thread().name)
        self.assertFalse(_bridge_lock.locked())

    def test_installing_twice_does_not_double_wrap(self):
        app, _ = self._server()
        _install_threaded_tool_dispatch(app)
        self.assertEqual(_install_threaded_tool_dispatch(app), 0)


if __name__ == "__main__":
    unittest.main()
