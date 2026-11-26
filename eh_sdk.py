"""Zero-dependency Python SDK & ACP (Agent Client Protocol) helper for Event Horizon.

Reuses the exact same bound AgentBridge, private submission journal, and safety
guardrails as the local stdio MCP server — without ever exposing plaintext API
keys to agent prompts or callers.

Quickstart (console-bound, recommended):
    from eh_sdk import EventHorizonClient

    eh = EventHorizonClient.from_console()
    print(eh.status())

    # 1. Inject directly into an ACP (Agent Client Protocol) session/new call:
    acp_servers = eh.acp_mcp_servers()

    # 2. Or drive your local connected Chrome directly from Python:
    session = eh.open_session(
        objective="Check the latest Python release version",
        start_url="https://www.python.org/",
    )
    obs = session.perceive()
    ...
    session.close()
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import uuid

HERE = Path(__file__).resolve().parent


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location("eh_sdk_" + name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EHSDKError(Exception):
    """Structured error raised when an Event Horizon tool call fails."""

    def __init__(self, code, payload=None):
        super().__init__(str(code))
        self.code = str(code)
        self.payload = payload if isinstance(payload, dict) else {"error": self.code}


def _extract_frame_id(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("frame_id", "next_frame_id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    core = payload.get("core_result")
    if isinstance(core, dict):
        val = core.get("frame_id")
        if isinstance(val, str) and val:
            return val
    return None


class EHSession:
    """Ergonomic handle for a single Event Horizon browser session."""

    def __init__(self, client, session_id, *, frame_id=None, created=None):
        self._client = client
        self.session_id = session_id
        self.frame_id = frame_id
        self.created = created or {}

    def _next_req(self, prefix, request_id=None):
        return request_id or f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _update_frame(self, payload):
        fid = _extract_frame_id(payload)
        if fid:
            self.frame_id = fid
        return payload

    def perceive(self, *, request_id=None, arguments=None, wait_seconds=None):
        args = {"session_id": self.session_id, "request_id": self._next_req("perceive", request_id)}
        if arguments is not None:
            args["arguments"] = dict(arguments)
        if wait_seconds is not None:
            args["wait_seconds"] = wait_seconds
        return self._update_frame(self._client.call("eh_perceive", args))

    def act(
        self,
        kind,
        *,
        target=None,
        query=None,
        fields=None,
        submit=None,
        decision=None,
        frame_id=None,
        request_id=None,
        wait_seconds=None,
    ):
        fid = frame_id or self.frame_id
        if not fid:
            raise EHSDKError("frame_id_required_call_perceive_first")
        args = {
            "session_id": self.session_id,
            "request_id": self._next_req("act", request_id),
            "frame_id": fid,
            "kind": kind,
        }
        if target is not None:
            args["target"] = target
        if query is not None:
            args["query"] = query
        if fields is not None:
            args["fields"] = fields
        if submit is not None:
            args["submit"] = bool(submit)
        if decision is not None:
            args["decision"] = decision
        if wait_seconds is not None:
            args["wait_seconds"] = wait_seconds
        return self._update_frame(self._client.call("eh_act", args))

    def act_batch(self, actions, *, decision=None, frame_id=None, request_id=None, wait_seconds=None):
        fid = frame_id or self.frame_id
        if not fid:
            raise EHSDKError("frame_id_required_call_perceive_first")
        args = {
            "session_id": self.session_id,
            "request_id": self._next_req("batch", request_id),
            "frame_id": fid,
            "actions": list(actions),
        }
        if decision is not None:
            args["decision"] = decision
        if wait_seconds is not None:
            args["wait_seconds"] = wait_seconds
        return self._update_frame(self._client.call("eh_act_batch", args))

    def terminal(
        self,
        status,
        conclusion,
        *,
        bindings=None,
        evidence=None,
        basis=None,
        frame_id=None,
        request_id=None,
        wait_seconds=None,
        close_session=False,
        **extra,
    ):
        fid = frame_id or self.frame_id
        if not fid:
            raise EHSDKError("frame_id_required_call_perceive_first")
        args = {
            "session_id": self.session_id,
            "request_id": self._next_req("term", request_id),
            "frame_id": fid,
            "status": status,
            "conclusion": conclusion,
        }
        if bindings is not None:
            args["bindings"] = bindings
        if evidence is not None:
            args["evidence"] = evidence
        if basis is not None:
            args["basis"] = basis
        if wait_seconds is not None:
            args["wait_seconds"] = wait_seconds
        if close_session:
            args["close_session"] = True
        args.update(extra)
        return self._client.call("eh_terminal", args)

    def evidence(self, *, ids=None, after=None, limit=None):
        args = {"session_id": self.session_id}
        if ids is not None:
            args["ids"] = list(ids)
        if after is not None:
            args["after"] = after
        if limit is not None:
            args["limit"] = int(limit)
        return self._client.call("eh_evidence", args)

    def tabs(self, *, after=None, limit=None):
        args = {"session_id": self.session_id}
        if after is not None:
            args["after"] = after
        if limit is not None:
            args["limit"] = int(limit)
        return self._client.call("eh_tabs", args)

    def result(self):
        return self._client.call("eh_result", {"session_id": self.session_id})

    def request_status(self, request_id, *, wait_seconds=None):
        args = {"session_id": self.session_id, "request_id": request_id}
        if wait_seconds is not None:
            args["wait_seconds"] = wait_seconds
        return self._update_frame(self._client.call("eh_request", args))

    def info(self):
        return self._client.call("eh_session", {"session_id": self.session_id})

    def close(self):
        return self._client.call("eh_close_session", {"session_id": self.session_id})


class EventHorizonClient:
    """Zero-dependency Python SDK for Event Horizon (console-bound or config-bound)."""

    def __init__(
        self,
        *,
        console_directory=None,
        config_path=None,
        directory=None,
        expected_account=None,
        expected_endpoint=None,
        auto_connect=False,
        idle_timeout=900.0,
        bridge=None,
    ):
        self._agent = _load_sibling("example_agent")
        self.console_directory = str(Path(os.path.expandvars(str(console_directory))).expanduser().absolute()) if console_directory else None
        self.config_path = str(Path(os.path.expandvars(str(config_path))).expanduser().absolute()) if config_path else None
        self.directory = str(Path(os.path.expandvars(str(directory))).expanduser().absolute()) if directory else None
        self.expected_account = expected_account
        self.expected_endpoint = expected_endpoint
        self._bridge = bridge or self._agent.AgentBridge(
            config_path=self.config_path,
            console_directory=self.console_directory,
            directory=self.directory,
            expected_account=self.expected_account,
            expected_endpoint=self.expected_endpoint,
            auto_connect=auto_connect,
            idle_timeout=idle_timeout,
        )

    @classmethod
    def from_console(cls, console_directory=None, *, expected_account=None, expected_endpoint=None,
                     auto_connect=False, idle_timeout=900.0):
        """Attach to the signed-in local EH console data directory without reading plaintext keys."""
        if console_directory is None:
            eh_mod = _load_sibling("eh")
            console_directory = eh_mod.default_directory()
        return cls(
            console_directory=console_directory,
            expected_account=expected_account,
            expected_endpoint=expected_endpoint,
            auto_connect=auto_connect,
            idle_timeout=idle_timeout,
        )

    @classmethod
    def from_config(cls, config_path, *, directory):
        """Attach using an explicit private API config file and submission journal directory."""
        return cls(config_path=config_path, directory=directory)

    def close(self):
        """Stop any on-demand browser/connector owned by this client."""
        close_fn = getattr(self._bridge, "close", None)
        if callable(close_fn):
            close_fn()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()
        return False

    def tools(self):
        """Return the list of all 17 EH tool definitions (JSON Schemas)."""
        return list(self._bridge.tools.values())

    def call(self, name, arguments=None):
        """Invoke any EH MCP tool by name through the shared bound AgentBridge."""
        if not isinstance(name, str) or name not in self._bridge.tools:
            raise EHSDKError("unknown_tool", {"error": "unknown_tool", "tool": name})
        args = {} if arguments is None else dict(arguments)
        try:
            raw = self._bridge.call(name, args)
        except Exception as exc:
            raise EHSDKError(str(exc)) from None
        text = raw.get("content", [{}])[0].get("text", "{}")
        payload = json.loads(text) if isinstance(text, str) else {}
        if raw.get("isError"):
            raise EHSDKError(payload.get("error", "tool_error"), payload)
        return payload

    def status(self):
        return self.call("eh_status", {})

    def capabilities(self):
        return self.call("eh_capabilities", {})

    def sessions(self, *, limit=20, after=None):
        args = {"limit": int(limit)}
        if after is not None:
            args["after"] = after
        return self.call("eh_sessions", args)

    def session(self, session_id=None, *, create_request_id=None):
        """Look up a session by session_id or create_request_id."""
        args = {}
        if session_id is not None:
            args["session_id"] = session_id
        if create_request_id is not None:
            args["create_request_id"] = create_request_id
        return self.call("eh_session", args)

    def open_session(
        self,
        objective,
        start_url,
        *,
        request_id=None,
        auto_perceive=False,
        perceive_request_id=None,
        wait_seconds=None,
    ):
        rid = request_id or f"create-{uuid.uuid4().hex[:12]}"
        args = {"objective": objective, "start_url": start_url, "request_id": rid}
        if auto_perceive:
            args["auto_perceive"] = True
            if perceive_request_id is not None:
                args["perceive_request_id"] = perceive_request_id
            if wait_seconds is not None:
                args["perceive_wait_seconds"] = wait_seconds
        created = self.call("eh_create_session", args)
        sid = created.get("session_id") or (created.get("session") or {}).get("id")
        if not isinstance(sid, str) or not sid:
            raise EHSDKError("missing_session_id", created)
        return EHSession(self, sid, frame_id=_extract_frame_id(created), created=created)

    def acp_mcp_servers(self, *, python=None):
        """Return an ACP session/new mcpServers array bound to this console directory."""
        if not self.console_directory:
            raise EHSDKError("acp_export_requires_console_directory")
        return self._agent.acp_config(
            self.console_directory,
            python=python,
            expected_account=self.expected_account,
            expected_endpoint=self.expected_endpoint,
        )

    def acp_mcp_server(self, *, python=None):
        """Return a single ACP mcpServer entry dict ready to append to session/new mcpServers."""
        return self.acp_mcp_servers(python=python)[0]
