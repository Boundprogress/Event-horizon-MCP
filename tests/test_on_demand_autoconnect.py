"""Tests for MCP/SDK on-demand browser + connector auto-launch, reuse, idle teardown,
safety boundaries, and panel.py browser_directory hash parity.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"test_ac_{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


example_agent = _load("example_agent")
eh_toolkit = _load("eh_toolkit")
eh_sdk = _load("eh_sdk")
panel = _load("panel")

SID = "c" * 32
FRAME = "d" * 32


def _make_console_dir(base: Path, *, with_device=True, pending=None, connection=None, endpoint="https://api.ehbrowser.com") -> Path:
    cdir = base / "console_ac"
    cdir.mkdir(mode=0o700, parents=True)
    os.chmod(cdir, 0o700)
    login_name = "credential-" + "1" * 32 + ".json"
    key_name = "credential-" + "2" * 32 + ".json"
    dev_name = "credential-" + "3" * 32 + ".json"
    for fname, tok in ((login_name, "ehs_" + "a" * 32), (key_name, "ehk_" + "b" * 32)):
        p = cdir / fname
        p.write_text(json.dumps({"endpoint": endpoint, "token": tok, "timeout": 15}), encoding="utf-8")
        os.chmod(p, 0o600)
    if with_device:
        dp = cdir / dev_name
        dp.write_text(json.dumps({
            "host": "hub.ehbrowser.com",
            "port": 443,
            "server_hostname": "hub.ehbrowser.com",
            "device_id": "dev_test01",
            "token": "ehd_" + "c" * 32,
        }), encoding="utf-8")
        os.chmod(dp, 0o600)
    state = {
        "schema": 1,
        "account": "a_testacct01",
        "endpoint": endpoint,
        "login_file": login_name,
        "key_file": key_name,
        "device_file": dev_name if with_device else None,
        "pending": pending,
        "connection": connection,
    }
    (cdir / "console.json").write_text(json.dumps(state), encoding="utf-8")
    os.chmod(cdir / "console.json", 0o600)
    return cdir


class _FakeAPI:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self._real = example_agent.sibling("client")

    def __getattr__(self, name):
        return getattr(self._real, name)

    def request(self, config, method, path, body=None):
        self.calls.append((method, path, body))
        return self.handler(method, path, body)


class _FakeConnector:
    def __init__(self, *, outcome=None, on_start=None):
        self.outcome = outcome or {"connected": True, "reused": False}
        self.on_start = on_start
        self.starts = []
        self.stops = 0
        self._running = False
        self._connected = False

    def is_running(self):
        return self._running

    def is_connected(self):
        return self._running and self._connected

    def start(self, api_path, device_path, browser_dir, *, chrome=None, headless=False):
        self.starts.append({
            "api_path": Path(api_path),
            "device_path": Path(device_path),
            "browser_dir": Path(browser_dir),
            "chrome": chrome,
            "headless": headless,
        })
        if self.on_start:
            self.on_start()
        if self.outcome.get("connected"):
            self._running = True
            self._connected = True
        return dict(self.outcome)

    def stop(self):
        self.stops += 1
        self._running = False
        self._connected = False


class OnDemandAutoConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".eh-ac-test-", dir=Path.home().resolve())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        os.chmod(self.root, 0o700)

    def test_browser_directory_hash_matches_panel_byte_for_byte(self):
        cdir = _make_console_dir(self.root)
        state = json.loads((cdir / "console.json").read_text(encoding="utf-8"))
        from_agent = example_agent._browser_directory_for_console(cdir, state)
        store = panel.Store(str(cdir), "https://api.ehbrowser.com")
        try:
            console = panel.Console(store)
            from_panel = console.browser_directory()
        finally:
            store.close()
        self.assertEqual(from_agent, from_panel)

    def test_status_never_launches_browser_but_reports_auto_connect_ready(self):
        cdir = _make_console_dir(self.root)
        conn = _FakeConnector()
        api = _FakeAPI(lambda m, p, b: ({
            "ok": True,
            "connector_readiness": {"available": True, "ready": False, "reason": "connector_offline", "checked_at": 100.0},
        }, 200))
        bridge = example_agent.AgentBridge(
            console_directory=str(cdir), api=api, auto_connect=True, idle_timeout=0,
            connector_factory=lambda: conn, clock=lambda: 100.0, sleep=lambda _: None,
        )
        self.addCleanup(bridge.close)
        raw = bridge.call("eh_status", {})
        body = json.loads(raw["content"][0]["text"])
        self.assertFalse(raw["isError"])
        self.assertEqual(len(conn.starts), 0)
        self.assertTrue(body["client"]["auto_connect"])
        self.assertTrue(body["client"]["auto_connect_ready"])
        self.assertFalse(body["client"]["auto_connect_running"])

    def test_create_session_auto_launches_visible_chrome_when_offline_and_reuses_next_call(self):
        cdir = _make_console_dir(self.root)
        online = [False]
        conn = _FakeConnector(on_start=lambda: online.__setitem__(0, True))

        def handler(m, p, b):
            if m == "GET" and p == "/health":
                return ({
                    "ok": True,
                    "connector_readiness": {
                        "available": True,
                        "ready": online[0],
                        "reason": "ready" if online[0] else "connector_offline",
                        "checked_at": 100.0,
                    },
                }, 200)
            if m == "POST" and p == "/session":
                self.assertTrue(online[0], "POST /session must run only after connector is online")
                return ({"session_id": SID, "lifecycle": "active"}, 201)
            return ({}, 404)

        api = _FakeAPI(handler)
        bridge = example_agent.AgentBridge(
            console_directory=str(cdir), api=api, auto_connect=True, idle_timeout=0,
            connector_factory=lambda: conn, clock=lambda: 100.0, sleep=lambda _: None,
        )
        self.addCleanup(bridge.close)
        raw = bridge.call("eh_create_session", {
            "request_id": "req-ac-1",
            "objective": "Check status",
            "start_url": "https://example.com/",
        })
        body = json.loads(raw["content"][0]["text"])
        self.assertFalse(raw["isError"])
        self.assertEqual(body["session_id"], SID)
        self.assertEqual(len(conn.starts), 1)
        self.assertFalse(conn.starts[0]["headless"])

        # Subsequent create_session reuses already-connected owned connector without re-launching
        raw2 = bridge.call("eh_create_session", {
            "request_id": "req-ac-2",
            "objective": "Second check",
            "start_url": "https://example.com/",
        })
        self.assertFalse(raw2["isError"])
        self.assertEqual(len(conn.starts), 1)

        bridge.close()
        self.assertEqual(conn.stops, 1)

    def test_create_session_reuses_external_ready_connector_without_launching(self):
        cdir = _make_console_dir(self.root)
        conn = _FakeConnector()

        def handler(m, p, b):
            if m == "GET" and p == "/health":
                return ({"ok": True, "connector_readiness": {"available": True, "ready": True, "reason": "ready", "checked_at": 100.0}}, 200)
            if m == "POST" and p == "/session":
                return ({"session_id": SID, "lifecycle": "active"}, 201)
            return ({}, 404)

        bridge = example_agent.AgentBridge(
            console_directory=str(cdir), api=_FakeAPI(handler), auto_connect=True, idle_timeout=0,
            connector_factory=lambda: conn, clock=lambda: 100.0, sleep=lambda _: None,
        )
        self.addCleanup(bridge.close)
        raw = bridge.call("eh_create_session", {
            "request_id": "req-ext-1",
            "objective": "Reuse console",
            "start_url": "https://example.com/",
        })
        self.assertFalse(raw["isError"])
        self.assertEqual(len(conn.starts), 0)

    def test_profile_in_use_race_reuses_concurrent_connector_when_health_becomes_ready(self):
        cdir = _make_console_dir(self.root)
        health_calls = [0]
        conn = _FakeConnector(outcome={"connected": False, "reason": "profile_in_use"})

        def handler(m, p, b):
            if m == "GET" and p == "/health":
                health_calls[0] += 1
                ready = health_calls[0] >= 2
                return ({"ok": True, "connector_readiness": {
                    "available": True, "ready": ready,
                    "reason": "ready" if ready else "connector_offline",
                    "checked_at": 100.0,
                }}, 200)
            if m == "POST" and p == "/session":
                return ({"session_id": SID, "lifecycle": "active"}, 201)
            return ({}, 404)

        t = [0.0]
        def fake_clock():
            t[0] += 0.1
            return t[0]

        bridge = example_agent.AgentBridge(
            console_directory=str(cdir), api=_FakeAPI(handler), auto_connect=True, idle_timeout=0,
            connector_factory=lambda: conn, clock=fake_clock, sleep=lambda _: None,
        )
        self.addCleanup(bridge.close)
        raw = bridge.call("eh_create_session", {
            "request_id": "req-race-1",
            "objective": "Concurrent host",
            "start_url": "https://example.com/",
        })
        self.assertFalse(raw["isError"])
        self.assertEqual(len(conn.starts), 1)

    def test_missing_device_or_dirty_connection_refuses_auto_launch_safely(self):
        cdir_nodev = _make_console_dir(self.root / "a", with_device=False)
        cdir_dirty = _make_console_dir(self.root / "b", with_device=True, connection={"status": "cleanup_unknown", "clean": False, "running": False})
        offline = lambda m, p, b: ({"ok": True, "connector_readiness": {"available": True, "ready": False, "reason": "connector_offline", "checked_at": 100.0}}, 200)

        for cdir, expected_err in ((cdir_nodev, "enroll_device_in_eh_client"), (cdir_dirty, "review_connection_in_eh_client")):
            conn = _FakeConnector()
            bridge = example_agent.AgentBridge(
                console_directory=str(cdir), api=_FakeAPI(offline), auto_connect=True, idle_timeout=0,
                connector_factory=lambda c=conn: c, clock=lambda: 100.0, sleep=lambda _: None,
            )
            self.addCleanup(bridge.close)
            with self.assertRaises(example_agent.AgentError) as ctx:
                bridge.call("eh_create_session", {
                    "request_id": "req-deny-1",
                    "objective": "Blocked",
                    "start_url": "https://example.com/",
                })
            self.assertEqual(str(ctx.exception), expected_err)
            self.assertEqual(len(conn.starts), 0)

    def test_simple_bridge_eh_open_allows_auto_connect_when_offline_and_closes_inner(self):
        cdir = _make_console_dir(self.root)
        online = [False]
        conn = _FakeConnector(on_start=lambda: online.__setitem__(0, True))

        def handler(m, p, b):
            if m == "GET" and p == "/health":
                return ({
                    "ok": True,
                    "active_sessions": 0,
                    "connector_readiness": {
                        "available": True, "ready": online[0],
                        "reason": "ready" if online[0] else "connector_offline",
                        "checked_at": 100.0,
                    },
                }, 200)
            if m == "POST" and p == "/session":
                return ({"session_id": SID, "lifecycle": "active"}, 201)
            if m == "POST" and p == "/command":
                return ({"status": "completed", "session_id": SID, "request_id": b["request_id"], "frame_id": FRAME,
                         "stdout": json.dumps({"ok": True, "v2_prompt": "# EH\nN1 item"})}, 200)
            return ({}, 404)

        inner = example_agent.AgentBridge(
            console_directory=str(cdir), api=_FakeAPI(handler), auto_connect=True, idle_timeout=0,
            connector_factory=lambda: conn, clock=lambda: 100.0, sleep=lambda _: None,
        )
        simple = eh_toolkit.SimpleBridge(inner)
        raw = simple.call("eh_open", {"objective": "Open demo", "start_url": "https://example.com/"})
        body = json.loads(raw["content"][0]["text"])
        self.assertFalse(raw["isError"])
        self.assertEqual(body["session_id"], SID)
        self.assertEqual(len(conn.starts), 1)
        simple.close()
        self.assertEqual(conn.stops, 1)

    def test_idle_watchdog_stops_connector_after_inactivity(self):
        cdir = _make_console_dir(self.root)
        online = [False]
        conn = _FakeConnector(on_start=lambda: online.__setitem__(0, True))

        def handler(m, p, b):
            if m == "GET" and p == "/health":
                return ({"ok": True, "connector_readiness": {
                    "available": True, "ready": online[0],
                    "reason": "ready" if online[0] else "connector_offline",
                    "checked_at": 100.0,
                }}, 200)
            if m == "POST" and p == "/session":
                return ({"session_id": SID, "lifecycle": "active"}, 201)
            return ({}, 404)

        bridge = example_agent.AgentBridge(
            console_directory=str(cdir), api=_FakeAPI(handler), auto_connect=True, idle_timeout=0.15,
            connector_factory=lambda: conn,
        )
        self.addCleanup(bridge.close)
        raw = bridge.call("eh_create_session", {
            "request_id": "req-idle-1",
            "objective": "Idle test",
            "start_url": "https://example.com/",
        })
        self.assertFalse(raw["isError"])
        self.assertEqual(len(conn.starts), 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and conn.stops == 0:
            time.sleep(0.05)
        self.assertGreaterEqual(conn.stops, 1)
        self.assertFalse(conn.is_running())

    def test_sdk_context_manager_passes_auto_connect_and_closes_cleanly(self):
        cdir = _make_console_dir(self.root)
        with eh_sdk.EventHorizonClient.from_console(str(cdir), auto_connect=True, idle_timeout=0) as eh:
            self.assertTrue(eh._bridge.auto_connect)
        self.assertTrue(eh._bridge._closed.is_set())


if __name__ == "__main__":
    unittest.main()
