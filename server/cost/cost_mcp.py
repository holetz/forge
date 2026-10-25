"""forge cost MCP server: cost per person, team, session and project, read-only.

Stdlib only. JSON-RPC 2.0 on POST /mcp (initialize, tools/list, tools/call). The data comes from a
reader; the only one shipped serves synthetic fixtures, so nothing here reaches Databricks.
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import cost
import fixtures

PROTOCOL = "2025-03-26"
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
WINDOW = {
    "since": {"type": "string", "description": "first day, YYYY-MM-DD, inclusive"},
    "until": {"type": "string", "description": "last day, YYYY-MM-DD, inclusive"},
}
SCHEMA = {"type": "object", "properties": WINDOW, "additionalProperties": False}
TOOLS = {
    "cost_by_person": "Spend per person, gateway tokens plus Databricks usage.",
    "cost_by_team": "Spend per team.",
    "cost_by_session": "Spend per session.",
    "cost_by_project": "Spend per project.",
    "budget_status": "Spend against each budget, with alerts at the warning fraction and the cap.",
}


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def window(args):
    out = {}
    for key in ("since", "until"):
        value = args.get(key)
        if value is not None and not (isinstance(value, str) and DAY.fullmatch(value)):
            raise RpcError(-32602, "%s must be YYYY-MM-DD" % key)
        out[key] = value
    return out


def call_tool(db, budgets, name, args):
    if name not in TOOLS:
        raise RpcError(-32602, "unknown tool %r" % name)
    unknown = set(args) - set(WINDOW)
    if unknown:
        raise RpcError(-32602, "unknown argument(s): %s" % ", ".join(sorted(unknown)))
    bounds = window(args)
    if name == "budget_status":
        return cost.budget_status(db, budgets, **bounds)
    return cost.cost_by(db, name[len("cost_by_"):], **bounds)


def handle(db, budgets, message):
    """One JSON-RPC request in, one response dict out (None for a notification)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    ident, method = message.get("id"), message["method"]
    if "id" not in message:
        return None
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "forge-cost", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": n, "description": d, "inputSchema": SCHEMA} for n, d in TOOLS.items()]}
        elif method == "tools/call":
            params = message.get("params") or {}
            data = call_tool(db, budgets, params.get("name"), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}
        else:
            raise RpcError(-32601, "method not found: %s" % method)
    except RpcError as err:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": err.code, "message": err.message}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def make_handler(reader, budgets):
    """The database is loaded on the first request, inside the one thread that serves: SQLite
    connections are bound to their thread, and a single-threaded server needs no lock."""
    state = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("cost: " + (fmt % args) + "\n")

        def _send(self, code, obj):
            data = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if urlsplit(self.path).path == "/healthz":
                return self._send(200, {"status": "ok"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlsplit(self.path).path != "/mcp":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                message = json.loads(self.rfile.read(length))
            except ValueError:
                return self._send(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            response = handle(state.setdefault("db", cost.load(reader)), budgets, message)
            self._send(202 if response is None else 200, response)

    return Handler


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    source = os.environ.get("COST_FIXTURE_FILE")
    reader = cost.FixtureReader(path=source) if source else cost.FixtureReader(fixtures.build())
    with open(os.environ.get("COST_BUDGETS_FILE", os.path.join(here, "budgets.json")), encoding="utf-8") as handle:
        budgets = json.load(handle)
    HTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8091"))), make_handler(reader, budgets)).serve_forever()


if __name__ == "__main__":
    main()
