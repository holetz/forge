"""forge MCP registry: the inventory of approved MCP servers, served read-only.

Stdlib only. Each entry names a gateway upstream; the endpoint a client receives is always the
egress gateway's, never the real server's, so an agent learns no real URL and holds no token.
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

REQUIRED = ("name", "description", "transport", "upstream", "roles", "enabled")
NAME = re.compile(r"[a-z0-9-]+")


def load_registry(path, upstreams_path):
    """Read the registry; refuse an entry that is malformed, repeated, or has no gateway upstream."""
    with open(path, encoding="utf-8") as handle:
        servers = json.load(handle)["servers"]
    with open(upstreams_path, encoding="utf-8") as handle:
        upstreams = json.load(handle)
    seen = set()
    for entry in servers:
        missing = [k for k in REQUIRED if k not in entry]
        if missing:
            raise ValueError("entry %r lacks %s" % (entry.get("name"), ", ".join(missing)))
        if not NAME.fullmatch(entry["name"]) or entry["name"] in seen:
            raise ValueError("entry name %r is invalid or repeated" % entry["name"])
        seen.add(entry["name"])
        if entry["upstream"] not in upstreams:
            raise ValueError("entry %s: upstream %r is not on the gateway allowlist" % (entry["name"], entry["upstream"]))
        if not isinstance(entry["roles"], list) or not isinstance(entry["enabled"], bool):
            raise ValueError("entry %s: roles must be a list and enabled a boolean" % entry["name"])
    return servers


def present(entry, gateway):
    shown = {k: entry[k] for k in REQUIRED if k != "upstream"}
    shown["endpoint"] = "%s/u/%s/" % (gateway.rstrip("/"), entry["upstream"])
    return shown


def make_handler(servers, gateway):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("registry: " + (fmt % args) + "\n")

        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._json(200, {"status": "ok"})
            if path == "/servers":
                return self._json(200, {"servers": [present(e, gateway) for e in servers]})
            match = re.fullmatch(r"/servers/([a-z0-9-]+)", path)
            for entry in servers:
                if match and entry["name"] == match.group(1):
                    return self._json(200, present(entry, gateway))
            self._json(404, {"error": "not found"})

    return Handler


def main():
    here = os.environ.get("REGISTRY_DIR", "/app")
    servers = load_registry(os.path.join(here, "registry.json"), os.environ.get("EGRESS_UPSTREAMS_FILE", "/egress/upstreams.json"))
    gateway = os.environ.get("REGISTRY_GATEWAY_URL", "http://egress:3128")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8090"))), make_handler(servers, gateway)).serve_forever()


if __name__ == "__main__":
    main()
