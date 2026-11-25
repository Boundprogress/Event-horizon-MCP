"""Tests for stdlib HTTP/1.1 keep-alive connection reuse and fail-closed POST safety."""
from __future__ import annotations

import gzip
import http.server
import importlib.util
import json
import socketserver
import threading
import unittest
from pathlib import Path

CLIENT_PATH = Path(__file__).resolve().parents[1] / "client.py"
_SPEC = importlib.util.spec_from_file_location("tested_eh_keepalive_client", CLIENT_PATH)
client = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(client)


class _CountingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_cls):
        self.accepted_connections = 0
        self.requests_seen = []
        self.mode = "normal"
        super().__init__(server_address, handler_cls)

    def get_request(self):
        conn, addr = super().get_request()
        self.accepted_connections += 1
        return conn, addr


class _KeepAliveHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _send_json(self, status, payload, *, close=False, compress=False, extra_headers=None):
        raw = json.dumps(payload).encode("utf-8")
        if compress:
            raw = gzip.compress(raw)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if compress:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(raw)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        else:
            self.send_header("Connection", "keep-alive")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.requests_seen.append(("GET", self.path))
        if self.path == "/redirect":
            self._send_json(302, {"error": "redirect"}, extra_headers={"Location": "/target"})
            return
        if self.path == "/gzip":
            self._send_json(200, {"ok": True, "compressed": True}, compress=True)
            return
        if self.path == "/drop-once" and self.server.mode == "drop_next_get":
            self.server.mode = "normal"
            self.close_connection = True
            self.connection.close()
            return
        if self.path == "/close-after":
            self._send_json(200, {"ok": True, "closed": True}, close=True)
            return
        self._send_json(200, {"ok": True, "path": self.path})

    def do_POST(self):
        _ = self._read_body()
        self.server.requests_seen.append(("POST", self.path))
        if self.path == "/drop-post":
            self.close_connection = True
            self.connection.close()
            return
        self._send_json(202, {"status": "pending", "ok": True})


class ClientKeepAliveTests(unittest.TestCase):
    def setUp(self):
        client._HTTP_POOL.close_all()
        self.server = _CountingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cfg = {
            "endpoint": f"http://127.0.0.1:{self.port}",
            "token": "secret-token-for-keepalive-test-1234",
            "timeout": 5.0,
        }

    def tearDown(self):
        client._HTTP_POOL.close_all()
        self.server.shutdown()
        self.server.server_close()

    def test_reuses_single_connection_across_post_and_get(self):
        v1, s1 = client.request(self.cfg, "POST", "/command", {"op": "perceive"})
        v2, s2 = client.request(self.cfg, "GET", "/request")
        v3, s3 = client.request(self.cfg, "GET", "/gzip")
        v4, s4 = client.request(self.cfg, "POST", "/command", {"op": "act"})
        self.assertEqual((s1, s2, s3, s4), (202, 200, 200, 202))
        self.assertTrue(v1["ok"] and v2["ok"] and v3["compressed"] and v4["ok"])
        self.assertEqual(self.server.accepted_connections, 1)

    def test_post_is_never_retried_on_dropped_connection(self):
        # Warm the pool first so the POST reuses a live socket.
        _, s0 = client.request(self.cfg, "GET", "/health")
        self.assertEqual(s0, 200)
        self.server.requests_seen.clear()
        val, status = client.request(self.cfg, "POST", "/drop-post", {"op": "act"})
        self.assertIsNone(status)
        self.assertEqual(val.get("status"), "uncertain")
        self.assertEqual(self.server.requests_seen, [("POST", "/drop-post")])

    def test_get_retries_once_when_pooled_connection_races_server_close(self):
        _, s0 = client.request(self.cfg, "GET", "/health")
        self.assertEqual(s0, 200)
        self.server.mode = "drop_next_get"
        val, status = client.request(self.cfg, "GET", "/drop-once")
        self.assertEqual(status, 200)
        self.assertTrue(val["ok"])
        self.assertEqual(self.server.accepted_connections, 2)

    def test_redirect_is_not_followed_and_not_pooled(self):
        val, status = client.request(self.cfg, "GET", "/redirect")
        self.assertEqual(status, 302)
        self.assertEqual(val.get("error"), "redirect")
        self.assertEqual(self.server.requests_seen, [("GET", "/redirect")])
        self.assertEqual(len(client._HTTP_POOL._idle), 0)

    def test_connection_close_header_not_pooled(self):
        val, status = client.request(self.cfg, "GET", "/close-after")
        self.assertEqual(status, 200)
        self.assertTrue(val["closed"])
        self.assertEqual(len(client._HTTP_POOL._idle), 0)

    def test_audit_reports_stdlib_only(self):
        import shutil
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "client.py"
            shutil.copy2(CLIENT_PATH, dst)
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(dst), "--json-only", "audit", "--package-dir", tmp],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertTrue(report["stdlib_only"], report["findings"])
            self.assertEqual(report["findings"], [])


if __name__ == "__main__":
    unittest.main()
