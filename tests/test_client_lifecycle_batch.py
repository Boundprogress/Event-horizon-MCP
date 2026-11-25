"""Tests for MCP/SDK lifecycle batching (auto_perceive + close_session)."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location("test_lc_" + name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eh_sdk = _load("eh_sdk")
example_agent = _load("example_agent")

SID = "a" * 32
FRAME = "f" * 32
QUOTA = {
    "available": True,
    "metering": "frames",
    "unit": "frame",
    "limit": 300,
    "used": 10,
    "reserved": 0,
    "remaining": 290,
    "terminal_units": 0,
}


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


class ClientLifecycleBatchTests(unittest.TestCase):
    def _make_bridge(self, handler):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        cfg_path = root / "config.json"
        cfg_path.write_text(json.dumps({
            "endpoint": "https://api.ehbrowser.test",
            "token": "tok_test_secret_1234567890",
            "timeout": 20,
        }))
        os.chmod(cfg_path, 0o600)
        journal_dir = root / "journal"
        journal_dir.mkdir(mode=0o700)
        api = _FakeAPI(handler)
        bridge = example_agent.AgentBridge(
            config_path=str(cfg_path),
            directory=str(journal_dir),
            api=api,
            clock=lambda: 100.0,
            sleep=lambda _s: None,
        )
        return bridge, api

    def _call_json(self, bridge, tool, args):
        raw = bridge.call(tool, args)
        return raw, json.loads(raw["content"][0]["text"])

    def test_create_session_auto_perceive_batches_create_and_perceive_and_replays_get_only(self):
        def handler(method, path, body):
            if method == "POST" and path == "/session":
                return {"session_id": SID, "lifecycle": "active", "subscription_quota": QUOTA}, 201
            if method == "GET" and path.startswith("/session?"):
                return {"session_id": SID, "lifecycle": "active", "subscription_quota": QUOTA}, 200
            if method == "POST" and path == "/command":
                self.assertEqual(body["op"], "perceive")
                self.assertEqual(body["request_id"], "sess-1-p0")
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": body["request_id"],
                    "frame_id": FRAME,
                    "stdout": json.dumps({"ok": True, "v2_prompt": "# EH 契约\nC0 item"}),
                }, 200
            if method == "GET" and path.startswith("/request?"):
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": "sess-1-p0",
                    "frame_id": FRAME,
                    "stdout": json.dumps({"ok": True, "v2_prompt": "# EH 契约\nC0 item"}),
                }, 200
            raise AssertionError(f"Unexpected call: {method} {path}")

        bridge, api = self._make_bridge(handler)
        args = {
            "request_id": "sess-1",
            "objective": "Find products",
            "start_url": "https://example.com/",
            "auto_perceive": True,
        }
        raw, body = self._call_json(bridge, "eh_create_session", args)
        self.assertFalse(raw["isError"])
        self.assertTrue(body["auto_perceived"])
        self.assertEqual(body["session_id"], SID)
        self.assertEqual(body["frame_id"], FRAME)
        self.assertEqual(body["perceive_request_id"], "sess-1-p0")
        self.assertEqual(body["core_result"]["v2_prompt"], "# EH 契约\nC0 item")
        self.assertEqual([m for m, _, _ in api.calls], ["POST", "POST"])

        # Repeating the exact same call must be strictly GET-only (no duplicate POSTs)
        raw2, body2 = self._call_json(bridge, "eh_create_session", args)
        self.assertFalse(raw2["isError"])
        self.assertTrue(body2["transport"]["repeat_is_query_only"])
        self.assertEqual([m for m, _, _ in api.calls[2:]], ["GET", "GET"])

    def test_create_session_auto_perceive_skips_perceive_on_create_error(self):
        def handler(method, path, body):
            if method == "POST" and path == "/session":
                return {"error": "connector_offline"}, 503
            raise AssertionError(f"Should not call perceive on failed create: {method} {path}")

        bridge, api = self._make_bridge(handler)
        raw, body = self._call_json(bridge, "eh_create_session", {
            "request_id": "sess-fail",
            "objective": "Find products",
            "start_url": "https://example.com/",
            "auto_perceive": True,
        })
        self.assertTrue(raw["isError"])
        self.assertFalse(body["auto_perceived"])
        self.assertEqual(len(api.calls), 1)

    def test_terminal_close_session_closes_after_committed_success(self):
        def handler(method, path, body):
            if method == "POST" and path == "/command":
                self.assertNotIn("close_session", body["args"])
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": body["request_id"],
                    "commit_state": "committed",
                    "stdout": json.dumps({"ok": True, "status": "success"}),
                }, 200
            if method == "POST" and path == "/session/close":
                self.assertEqual(body, {"session_id": SID})
                return {"session_id": SID, "lifecycle": "closed"}, 200
            raise AssertionError(f"Unexpected call: {method} {path}")

        bridge, api = self._make_bridge(handler)
        raw, body = self._call_json(bridge, "eh_terminal", {
            "session_id": SID,
            "request_id": "term-1",
            "frame_id": FRAME,
            "status": "success",
            "conclusion": "Done",
            "bindings": {"item": "C0"},
            "close_session": True,
        })
        self.assertFalse(raw["isError"])
        self.assertTrue(body["session_closed"])
        self.assertEqual(body["close_http_status"], 200)
        self.assertEqual(body["lifecycle"], "closed")
        self.assertEqual([(m, p) for m, p, _ in api.calls], [("POST", "/command"), ("POST", "/session/close")])

    def test_terminal_close_session_does_not_close_when_terminal_rejected(self):
        def handler(method, path, body):
            if method == "POST" and path == "/command":
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": body["request_id"],
                    "stdout": json.dumps({"ok": False, "error": "binding_mismatch"}),
                }, 200
            raise AssertionError(f"Must NOT close on failed terminal: {method} {path}")

        bridge, api = self._make_bridge(handler)
        raw, body = self._call_json(bridge, "eh_terminal", {
            "session_id": SID,
            "request_id": "term-reject",
            "frame_id": FRAME,
            "status": "success",
            "conclusion": "Done",
            "bindings": {"item": "C0"},
            "close_session": True,
        })
        self.assertTrue(raw["isError"])
        self.assertFalse(body["session_closed"])
        self.assertEqual(len(api.calls), 1)

    def test_sdk_open_session_auto_perceive_and_terminal_close_session(self):
        def handler(method, path, body):
            if method == "POST" and path == "/session":
                return {"session_id": SID, "lifecycle": "active", "subscription_quota": QUOTA}, 201
            if method == "POST" and path == "/command" and body["op"] == "perceive":
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": body["request_id"],
                    "frame_id": FRAME,
                    "stdout": json.dumps({"ok": True, "v2_prompt": "# EH 契约\nC0 ok"}),
                }, 200
            if method == "POST" and path == "/command" and body["op"] == "terminal":
                return {
                    "status": "completed",
                    "session_id": SID,
                    "request_id": body["request_id"],
                    "stdout": json.dumps({"ok": True, "status": "success"}),
                }, 200
            if method == "POST" and path == "/session/close":
                return {"session_id": SID, "lifecycle": "closed"}, 200
            raise AssertionError(f"Unexpected call: {method} {path}")

        bridge, _ = self._make_bridge(handler)
        client = eh_sdk.EventHorizonClient(bridge=bridge)
        session = client.open_session("Check page", "https://example.com/", auto_perceive=True)
        self.assertEqual(session.session_id, SID)
        self.assertEqual(session.frame_id, FRAME)
        res = session.terminal(
            "success", "Done",
            bindings={"item": "C0"},
            close_session=True,
        )
        self.assertTrue(res["session_closed"])


if __name__ == "__main__":
    unittest.main()
