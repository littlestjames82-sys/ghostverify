"""MCP server for GhostVerify: stdio JSON-RPC, stdlib only.

Same pattern as GhostSlice's MCP server: newline-delimited
JSON-RPC 2.0 over stdin/stdout, no network. Tools return capped
summaries (gap lists capped at 50), never raw file bodies.
"""
from __future__ import annotations

import json
import sys

from . import __version__
from .core import run_fix, run_post, run_pre

PROTOCOL_VERSION = "2024-11-05"
GAP_CAP = 50

TOOLS = [
    {"name": "verify_bundle",
     "description": "Pre-flight sufficiency check of a GhostSlice bundle against its project: hard gaps (reachable symbols absent/excluded), soft gaps (heuristic), GhostSlice lint/secrets carry-over. Returns a capped summary, no file bodies.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "bundle": {"type": "string"},
                                    "task": {"type": "string", "default": ""}},
                     "required": ["project", "bundle"]}},
    {"name": "expand_bundle",
     "description": "Auto-expand a GhostSlice bundle via GhostSlice re-export until the pre-check passes or no progress (max 3 rounds). Writes the expanded bundle to 'out' and returns round deltas.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "bundle": {"type": "string"},
                                    "out": {"type": "string"},
                                    "task": {"type": "string", "default": ""}},
                     "required": ["project", "bundle", "out"]}},
    {"name": "check_patch",
     "description": "Static post-run patch check: applies the patch to a temporary copy only (never the working tree), flags files/symbols outside the bundle and test edits. Tests run only when run_tests is true and a test_command is given.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "bundle": {"type": "string"},
                                    "patch": {"type": "string"},
                                    "task": {"type": "string", "default": ""},
                                    "run_tests": {"type": "boolean", "default": False},
                                    "test_command": {"type": "string"}},
                     "required": ["project", "bundle", "patch"]}},
]


def _capped(report: dict) -> dict:
    out = {k: v for k, v in report.items() if k not in ("carryover",)}
    for key in ("gaps", "soft_gaps"):
        rows = out.get(key) or []
        if len(rows) > GAP_CAP:
            out[key] = rows[:GAP_CAP]
            out[key + "_truncated"] = True
            out[key + "_total"] = len(rows)
    carry = report.get("carryover") or {}
    out["carryover_counts"] = {
        "lint": len(carry.get("lint", [])),
        "secrets": len(carry.get("secrets", [])),
        "unavailable": carry.get("unavailable"),
    }
    return out


def _verify_bundle(args):
    return _capped(run_pre(args["project"], args["bundle"], args.get("task", "")))


def _expand_bundle(args):
    return _capped(run_fix(args["project"], args["bundle"], args["out"],
                           args.get("task", "")))


def _check_patch(args):
    report = run_post(args["project"], args["bundle"], args["patch"],
                      args.get("task", ""),
                      run_tests=bool(args.get("run_tests")),
                      test_command=args.get("test_command"))
    return _capped(report)


_DISPATCH = {"verify_bundle": _verify_bundle,
             "expand_bundle": _expand_bundle,
             "check_patch": _check_patch}


def handle_message(msg):
    """Pure JSON-RPC handler (testable without stdio)."""
    if not isinstance(msg, dict):
        return None
    method = msg.get("method") or ""
    if not isinstance(method, str):
        method = ""
    msg_id = msg.get("id")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION,
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "ghostverify", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = params.get("name", "")
        fn = _DISPATCH.get(name) if isinstance(name, str) else None
        if fn is None:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            payload = fn(params.get("arguments") or {})
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload, indent=2)}],
                      "isError": False}
        except Exception as exc:
            result = {"content": [{"type": "text", "text": f"error: {exc}"}],
                      "isError": True}
    else:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601,
                          "message": f"method not found: {method}"}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def run_stdio(instream=None, outstream=None) -> None:
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    for line in instream:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        resp = handle_message(msg)
        if resp is not None:
            outstream.write(json.dumps(resp) + "\n")
            outstream.flush()
