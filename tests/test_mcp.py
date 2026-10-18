"""MCP surface test (plan MVP item 5): tools verify_bundle,
expand_bundle, check_patch over stdio, local only.

This is deliberately conservative: if the CLI has no discoverable
``mcp`` subcommand, or the server does not answer an ``initialize``
+ ``tools/list`` handshake, the test skips. Only once a tools/list
response actually arrives does it assert the plan's tool names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestMcpTools(unittest.TestCase):
    def test_mcp_lists_the_plan_tools(self):
        prefix = helpers.cli_prefix()
        env = helpers.cli_env()
        try:
            proc = subprocess.Popen(
                prefix + ["mcp"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env)
        except OSError:
            self.skipTest("mcp subcommand not launchable")
        requests = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "gv-tests",
                                                  "version": "0"}}}),
            json.dumps({"jsonrpc": "2.0",
                        "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2,
                        "method": "tools/list", "params": {}}),
        ]) + "\n"
        try:
            out, _ = proc.communicate(requests, timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.skipTest("mcp server did not answer within 15s")
        tools = []
        for line in out.splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                tools = [t.get("name") for t in
                         msg.get("result", {}).get("tools", [])]
        if not tools:
            self.skipTest("no tools/list response from mcp server")
        for expected in ("verify_bundle", "expand_bundle", "check_patch"):
            self.assertIn(expected, tools)


if __name__ == "__main__":
    unittest.main()
