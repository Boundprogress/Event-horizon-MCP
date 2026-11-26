"""Unit tests for pure-stdlib eh_toolkit.py (5-tool lean atomic --profile simple)."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location("test_tk_" + name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


example_agent = _load("example_agent")
eh_toolkit = _load("eh_toolkit")

SID = "b" * 32
F0 = "0" * 32
F1 = "1" * 32
F2 = "2" * 32
HEALTH_READY = {
    "status": "ok",
    "active_sessions": 0,
    "entry_policy": {"available": True, "schema": 2, "mode": "public_except_sensitive"},
    "connector_readiness": {"available": True, "ready": True, "reason": "ready", "checked_at": 1795600703.5},
    "subscription_quota": {
        "available": True, "metering": "frames", "unit": "frame",
        "limit": 1000, "used": 1, "reserved": 0, "remaining": 999, "terminal_units": 0,
    },
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


class ClientToolkitTests(unittest.TestCase):
    def _make_simple(self, handler):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        cfg = root / "config.json"
        cfg.write_text(json.dumps({
            "endpoint": "https://api.ehbrowser.test",
            "token": "tok_test_secret_1234567890",
            "timeout": 20,
        }))
        os.chmod(cfg, 0o600)
        jdir = root / "journal"
        jdir.mkdir(mode=0o700)
        api = _FakeAPI(handler)
        inner = example_agent.AgentBridge(
            config_path=str(cfg),
            directory=str(jdir),
            api=api,
            clock=lambda: 200.0,
            sleep=lambda _s: None,
        )
        return eh_toolkit.SimpleBridge(inner), api

    def _call(self, bridge, name, args):
        raw = bridge.call(name, args)
        return raw, json.loads(raw["content"][0]["text"])

    def test_simple_profile_exposes_5_lean_atomic_tools_with_tiny_schema(self):
        bridge, _ = self._make_simple(lambda m, p, b: ({}, 200))
        server = example_agent.MCPServer(bridge)
        init = server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        })
        self.assertIn("Simple Profile", init["result"]["instructions"])
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        listed = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in listed["result"]["tools"]]
        self.assertEqual(names, ["eh_status", "eh_open", "eh_act", "eh_finish", "eh_recover"])
        simple_bytes = len(json.dumps(listed["result"]["tools"]))
        full_bytes = len(json.dumps(example_agent.tool_definitions()))
        self.assertLess(simple_bytes, full_bytes * 0.35)

    def test_eh_open_fails_fast_when_connector_offline(self):
        def handler(method, path, body):
            if method == "GET" and path == "/health":
                return dict(HEALTH_READY, connector_readiness={"available": True, "ready": False, "reason": "connector_offline", "checked_at": 1.0}), 200
            raise AssertionError(f"Must not create session when offline: {method} {path}")

        bridge, api = self._make_simple(handler)
        raw, body = self._call(bridge, "eh_open", {"objective": "Search", "start_url": "https://example.com/"})
        self.assertTrue(raw["isError"])
        self.assertEqual(body["error"], "connector_not_ready")
        self.assertEqual(len(api.calls), 1)

    def test_atomic_flow_open_fill_scroll_capture_perceive_and_finish_over_12_bindings(self):
        seen = {}

        def handler(method, path, body):
            if method == "GET" and path == "/health":
                return HEALTH_READY, 200
            if method == "POST" and path == "/session":
                return {"session_id": SID, "lifecycle": "active", "subscription_quota": HEALTH_READY["subscription_quota"]}, 201
            if method == "POST" and path == "/command":
                op = body["op"]
                if op == "perceive":
                    fid = F0 if "p0" not in seen else F2
                    seen["p0"] = True
                    return {
                        "status": "completed", "session_id": SID, "request_id": body["request_id"], "frame_id": fid,
                        "stdout": json.dumps({"ok": True, "v2_prompt": "N35 Search Amazon 可编辑"}),
                    }, 200
                if op == "act":
                    kind = body["args"]["kind"]
                    if kind == "fill":
                        self.assertTrue(body["args"]["submit"])
                        return {
                            "status": "completed", "session_id": SID, "request_id": body["request_id"], "frame_id": F1,
                            "stdout": json.dumps({
                                "ok": True, "navigated": True,
                                "observation": {"v2_prompt": "N100 Result 1 $32.96", "prompt_delivery": {"mode": "full"}},
                            }),
                        }, 200
                    if kind == "scroll":
                        seen["ep"] = body["args"]["decision"].get("evidence_patch")
                        return {
                            "status": "completed", "session_id": SID, "request_id": body["request_id"], "frame_id": F2,
                            "stdout": json.dumps({
                                "ok": True,
                                "audited_evidence": {"captured": [{"id": "E0", "name": "r1"}]},
                                "observation": {"v2_prompt": "N200 Result 2 $55.58", "prompt_delivery": {"mode": "delta"}},
                            }),
                        }, 200
                    if kind == "expand":
                        seen["expand_target"] = body["args"].get("target")
                        return {
                            "status": "completed", "session_id": SID, "request_id": body["request_id"], "frame_id": F2,
                            "stdout": json.dumps({"ok": True, "observation": {"v2_prompt": "FULL"}}),
                        }, 200
                if op == "terminal":
                    seen["term_evidence"] = body["args"].get("evidence")
                    return {
                        "status": "completed", "session_id": SID, "request_id": body["request_id"],
                        "commit_state": "committed",
                        "stdout": json.dumps({"ok": True, "status": "success"}),
                    }, 200
            if method == "POST" and path == "/session/close":
                return {"session_id": SID, "lifecycle": "closed"}, 200
            raise AssertionError(f"Unexpected call: {method} {path} {body}")

        bridge, _ = self._make_simple(handler)
        raw, opened = self._call(bridge, "eh_open", {"objective": "Find keyboards", "start_url": "https://www.amazon.com/"})
        self.assertFalse(raw["isError"])
        self.assertEqual(opened["frame_id"], F0)

        # Fill with submit=True (no session_id/frame_id/request_id needed!)
        raw, fl = self._call(bridge, "eh_act", {"kind": "fill", "target": "N35", "query": "mechanical keyboard", "submit": True})
        self.assertFalse(raw["isError"])
        self.assertEqual(fl["frame_id"], F1)

        # Single scroll + lockstep E* capture (strictly without "collection")
        raw, sc = self._call(bridge, "eh_act", {
            "kind": "scroll", "query": "down",
            "capture": [{"id": "N100", "name": "r1"}],
        })
        self.assertFalse(raw["isError"])
        self.assertEqual(seen["ep"], {"capture": [{"id": "N100", "name": "r1"}]})
        self.assertEqual(sc["delivery_mode"], "delta")

        # Expand drops leaf node target cleanly
        raw, _ = self._call(bridge, "eh_act", {"kind": "expand", "target": "N200"})
        self.assertFalse(raw["isError"])
        self.assertIsNone(seen["expand_target"])

        # Explicit kind="perceive" routes to eh_perceive
        raw, pc = self._call(bridge, "eh_act", {"kind": "perceive"})
        self.assertFalse(raw["isError"])
        self.assertEqual(pc["frame_id"], F2)

        # Finish with >12 bindings auto-populates evidence[:12] and closes session
        bindings = {f"item_{i}": f"E{i}" for i in range(15)}
        raw, fin = self._call(bridge, "eh_finish", {
            "status": "success", "conclusion": "15 items captured", "bindings": bindings,
        })
        self.assertFalse(raw["isError"])
        self.assertEqual(len(seen["term_evidence"]), 12)
        self.assertTrue(fin["session_closed"])
        self.assertIsNone(bridge.active_session_id)


if __name__ == "__main__":
    unittest.main()
