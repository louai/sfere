"""The MCP server's protocol and the control socket (the full path against a real window is
exercised by hand: see README, Agent control)."""
from __future__ import annotations

import threading

from sfere import control, mcp


def test_mcp_handshake_and_tools():
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "sfere" and "instructions" in init["result"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"status", "get_scene", "set_layer", "bind", "record", "virtual_camera", "screenshot"} <= names
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    # every tool maps to a command the app implements
    assert all(hasattr(control.Commands, f"cmd_{c}") for c in mcp.COMMANDS.values())
    assert mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_tool_call_when_sfere_isnt_running(tmp_path, monkeypatch):
    monkeypatch.setenv("SFERE_CONTROL_SOCKET", str(tmp_path / "none.sock"))
    r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "status", "arguments": {}}})
    assert r["result"]["isError"] and "isn't running" in r["result"]["content"][0]["text"]


def test_control_socket_runs_commands_on_the_polling_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("SFERE_CONTROL_SOCKET", str(tmp_path / "c.sock"))
    server = control.ControlServer(app=None)
    assert not server.error
    seen = []
    server.commands.cmd_echo = lambda **kw: seen.append(threading.current_thread().name) or {"got": kw}
    stop = threading.Event()

    def main_loop():               # stands in for App.frame()
        while not stop.is_set():
            server.poll()
            stop.wait(0.01)
    t = threading.Thread(target=main_loop, name="main-loop")
    t.start()
    try:
        assert control.send_control({"cmd": "ping"}) == {"ok": True}
        assert control.send_control({"cmd": "echo", "x": [1, 2]}) == {"got": {"x": [1, 2]}, "ok": True}
        assert seen == ["main-loop"]
        assert not control.send_control({"cmd": "no_such"})["ok"]
        # a second sfere doesn't take the socket from the first
        assert "another sfere" in control.ControlServer(app=None).error
    finally:
        stop.set()
        t.join()
        server.close()
    assert not (tmp_path / "c.sock").exists()
