"""Tests for ACP mcpServers export, 3-tier CONNECT.md, bound CLI `client.py tool`,
and the zero-dependency Python SDK (eh_sdk.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"test_acp_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = _load_sibling("client")
eh_sdk = _load_sibling("eh_sdk")
example_agent = _load_sibling("example_agent")


def _make_console_dir(base: Path) -> Path:
    cdir = base / "console_data"
    cdir.mkdir(mode=0o700)
    os.chmod(cdir, 0o700)
    login_name = "credential-" + "1" * 32 + ".json"
    key_name = "credential-" + "2" * 32 + ".json"
    endpoint = "https://api.ehbrowser.com"
    for fname, tok in ((login_name, "ehs_" + "a" * 32), (key_name, "ehk_" + "b" * 32)):
        p = cdir / fname
        p.write_text(json.dumps({"endpoint": endpoint, "token": tok, "timeout": 15}), encoding="utf-8")
        os.chmod(p, 0o600)
    (cdir / "console.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "account": "a_testacct01",
                "endpoint": endpoint,
                "login_file": login_name,
                "key_file": key_name,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(cdir / "console.json", 0o600)
    return cdir


SID = "0123456789abcdef0123456789abcdef"


class FakeBridge:
    def __init__(self):
        self.tools = {t["name"]: t for t in example_agent.tool_definitions()}
        self.calls = []

    def call(self, name, arguments):
        example_agent._validate(arguments, self.tools[name]["inputSchema"])
        self.calls.append((name, dict(arguments)))
        if name == "eh_status":
            return {"content": [{"type": "text", "text": json.dumps({"ok": True, "account": "a_testacct01"})}], "isError": False}
        if name == "eh_capabilities":
            return {"content": [{"type": "text", "text": json.dumps({"available": True})}], "isError": False}
        if name == "eh_sessions":
            return {"content": [{"type": "text", "text": json.dumps({"sessions": []})}], "isError": False}
        if name == "eh_session":
            return {"content": [{"type": "text", "text": json.dumps({"session_id": SID, "lifecycle": "active"})}], "isError": False}
        if name == "eh_create_session":
            return {"content": [{"type": "text", "text": json.dumps({"session_id": SID, "frame_id": "f_0"})}], "isError": False}
        if name == "eh_perceive":
            return {"content": [{"type": "text", "text": json.dumps({"frame_id": "f_1", "core_result": {"v2_prompt": "[Page] Demo"}})}], "isError": False}
        if name == "eh_act":
            return {"content": [{"type": "text", "text": json.dumps({"frame_id": "f_2", "status": "completed"})}], "isError": False}
        if name == "eh_act_batch":
            return {"content": [{"type": "text", "text": json.dumps({"frame_id": "f_3", "status": "completed"})}], "isError": False}
        if name == "eh_evidence":
            return {"content": [{"type": "text", "text": json.dumps({"items": []})}], "isError": False}
        if name == "eh_tabs":
            return {"content": [{"type": "text", "text": json.dumps({"tabs": []})}], "isError": False}
        if name == "eh_result":
            return {"content": [{"type": "text", "text": json.dumps({"commit_state": "committed"})}], "isError": False}
        if name == "eh_request":
            return {"content": [{"type": "text", "text": json.dumps({"status": "completed", "frame_id": "f_3"})}], "isError": False}
        if name == "eh_terminal":
            return {"content": [{"type": "text", "text": json.dumps({"commit_state": "committed"})}], "isError": False}
        if name == "eh_close_session":
            return {"content": [{"type": "text", "text": json.dumps({"closed": True})}], "isError": False}
        return {"content": [{"type": "text", "text": json.dumps({"error": "unexpected_tool"})}], "isError": True}


class ACPSDKAndCLIToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".eh-sdk-test-", dir=Path.home().resolve())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        os.chmod(self.root, 0o700)
        self.cdir = _make_console_dir(self.root)

    def test_acp_export_and_cli_mcp_config(self):
        exported = example_agent.mcp_export(
            self.cdir,
            host="acp",
            expected_account="a_testacct01",
            expected_endpoint="https://api.ehbrowser.com",
        )
        self.assertEqual(exported["host"], "acp")
        self.assertEqual(exported["format"], "json")
        servers = json.loads(exported["text"])
        self.assertIsInstance(servers, list)
        self.assertEqual(len(servers), 1)
        entry = servers[0]
        self.assertEqual(entry["name"], "event-horizon")
        self.assertEqual(entry["env"], [])
        self.assertIn("mcp", entry["args"])
        self.assertIn("--expected-account", entry["args"])
        self.assertNotIn("ehk_", exported["text"])
        self.assertNotIn("ehs_", exported["text"])

        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(ROOT / "client.py"), "mcp-config",
             "--console-directory", str(self.cdir), "--host", "acp"],
            capture_output=True, text=True, check=True,
        )
        cli_servers = json.loads(proc.stdout)
        self.assertEqual(cli_servers[0]["name"], "event-horizon")
        self.assertEqual(cli_servers[0]["env"], [])

    def test_prepare_agent_link_writes_3_tier_connect_and_acp_file_without_secrets(self):
        res = example_agent.prepare_agent_link(self.cdir, host="generic")
        self.assertFalse(res["contains_credentials"])
        self.assertIn("acp-mcp-servers.json", res["files"])
        stage = Path(res["directory"])
        acp_json = json.loads((stage / "acp-mcp-servers.json").read_text(encoding="utf-8"))
        self.assertEqual(acp_json[0]["name"], "event-horizon")
        self.assertEqual(acp_json[0]["env"], [])

        connect_md = (stage / "CONNECT.md").read_text(encoding="utf-8")
        self.assertIn("acp-mcp-servers.json", connect_md)
        self.assertIn("eh_sdk.py", connect_md)
        self.assertIn("tool --console-directory", connect_md)
        self.assertIn("--name eh_status", connect_md)
        self.assertNotIn("ehk_", connect_md)
        self.assertNotIn("ehs_", connect_md)

    def test_cli_tool_list_exposes_all_17_tools_via_bound_console(self):
        proc = subprocess.run(
            [
                sys.executable, "-I", "-S", "-B", str(ROOT / "client.py"), "tool",
                "--console-directory", str(self.cdir),
                "--expected-account", "a_testacct01",
                "--expected-endpoint", "https://api.ehbrowser.com",
                "--list",
            ],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(proc.stdout)
        names = {t["name"] for t in data["tools"]}
        self.assertGreaterEqual(len(names), 17)
        for required in ("eh_status", "eh_sessions", "eh_create_session", "eh_perceive", "eh_act", "eh_terminal", "eh_close_session"):
            self.assertIn(required, names)
        self.assertNotIn("ehk_", proc.stdout)
        self.assertNotIn("ehs_", proc.stdout)

    def test_sdk_lifecycle_and_acp_helper(self):
        fake = FakeBridge()
        sdk = eh_sdk.EventHorizonClient(
            console_directory=self.cdir,
            expected_account="a_testacct01",
            expected_endpoint="https://api.ehbrowser.com",
            bridge=fake,
        )
        self.assertTrue(sdk.status()["ok"])
        self.assertTrue(sdk.capabilities()["available"])
        self.assertEqual(sdk.sessions()["sessions"], [])
        self.assertEqual(sdk.session(SID)["session_id"], SID)
        acp_entry = sdk.acp_mcp_server()
        self.assertEqual(acp_entry["name"], "event-horizon")
        self.assertEqual(acp_entry["env"], [])

        sess = sdk.open_session("Read Python version", "https://www.python.org/")
        create_call = next(args for name, args in fake.calls if name == "eh_create_session")
        self.assertEqual(create_call["objective"], "Read Python version")
        self.assertNotIn("task", create_call)
        self.assertEqual(sess.session_id, SID)
        self.assertEqual(sess.frame_id, "f_0")
        obs = sess.perceive(arguments={"query": "version"}, wait_seconds=30)
        self.assertEqual(obs["frame_id"], "f_1")
        self.assertEqual(sess.frame_id, "f_1")
        decision = {
            "subgoal": "open",
            "evidence": ["N1"],
            "basis": ["visible_text"],
            "expected": "target opens",
            "confidence": "high",
        }
        act = sess.act("click", target="N1", decision=decision)
        self.assertEqual(act["frame_id"], "f_2")
        self.assertEqual(sess.frame_id, "f_2")
        batch = sess.act_batch(
            [{"kind": "click", "target": "N1"}],
            decision=decision,
        )
        self.assertEqual(batch["frame_id"], "f_3")
        self.assertEqual(sess.evidence(limit=5)["items"], [])
        self.assertEqual(sess.tabs(limit=5)["tabs"], [])
        self.assertEqual(sess.request_status("req-1")["status"], "completed")
        self.assertEqual(sess.info()["lifecycle"], "active")
        term = sess.terminal("success", "Python 3.12", bindings={"version": "N1"})
        self.assertEqual(term["commit_state"], "committed")
        self.assertEqual(sess.result()["commit_state"], "committed")
        self.assertTrue(sess.close()["closed"])

        with self.assertRaises(eh_sdk.EHSDKError):
            sdk.call("nonexistent_tool")

    def test_package_audit_passes_with_eh_sdk_stdlib_only(self):
        clean_dir = Path(self.tmp.name) / "clean_pkg"
        shutil.copytree(
            ROOT,
            clean_dir,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git", ".DS_Store", "*.pyc"),
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(clean_dir / "client.py"), "audit", "--package-dir", str(clean_dir), "--json-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["stdlib_only"], data.get("findings"))
        self.assertEqual(data.get("findings"), [])
        audited_paths = {item["path"] for item in data["files"]}
        self.assertIn("eh_sdk.py", audited_paths)
        self.assertIn("tests/test_acp_sdk_and_cli_tool.py", audited_paths)


if __name__ == "__main__":
    unittest.main()
