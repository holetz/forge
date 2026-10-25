"""forge task router: routes OpenAI-compatible chat requests to an upstream by task.

Stdlib only. A request's route is picked from the X-Forge-Task header; the body's model is
rewritten to the route's model, the caller's credential is dropped and the route's token (from the
environment) is injected. The response, SSE included, is streamed back unchanged.
"""
import http.client
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ESCALATION_TASKS = {"security", "architecture", "repeated-failure"}
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
              "proxy-authenticate", "proxy-authorization"}
PLAIN_HTTP_HOSTS_DEFAULT = "stub"
IDENTITY_HEADERS = ("X-Forge-User", "X-Forge-Session")


def classify(task_header):
    """Return the route name for an X-Forge-Task header value."""
    task = (task_header or "").strip().lower()
    return "escalation" if task in ESCALATION_TASKS else "default"


def load_routes(env):
    """Build the route table from FORGE_<ROUTE>_{BASE_URL,MODEL,TOKEN}; refuse clear-text upstreams.

    Plain HTTP is allowed only to the hosts named in FORGE_PLAIN_HTTP_HOSTS (default: the stub).
    """
    plain_ok = {h.strip() for h in env.get("FORGE_PLAIN_HTTP_HOSTS", PLAIN_HTTP_HOSTS_DEFAULT).split(",") if h.strip()}
    routes = {}
    for name in ("default", "escalation"):
        prefix = "FORGE_%s_" % name.upper()
        base = env.get(prefix + "BASE_URL", "").strip().rstrip("/")
        model = env.get(prefix + "MODEL", "").strip()
        token = env.get(prefix + "TOKEN", "").strip()
        if not (base and model and token):
            raise ValueError("route %s needs %sBASE_URL, %sMODEL and %sTOKEN" % (name, prefix, prefix, prefix))
        parts = urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("route %s: invalid base url" % name)
        if parts.scheme == "http" and parts.hostname not in plain_ok:
            raise ValueError("route %s: plain http upstream refused (only %s may use it)" % (name, ", ".join(sorted(plain_ok))))
        routes[name] = {"base": base, "model": model, "token": token}
    return routes


def rewrite_body(raw, model):
    """Return the request body with `model` replaced; non-JSON bodies are refused."""
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    body["model"] = model
    return json.dumps(body).encode()


def upstream_headers(route, content_length, incoming=None):
    """Upstream headers: the route's credential, plus the caller's identity headers for the audit."""
    headers = {"Content-Type": "application/json", "Content-Length": str(content_length),
               "Authorization": "Bearer " + route["token"]}
    for name in IDENTITY_HEADERS:
        value = incoming.get(name) if incoming is not None else None
        if value:
            headers[name] = value
    return headers


def make_handler(routes):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("router: " + (fmt % args) + "\n")

        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                self._json(200, {"status": "ok"})
            elif self.path in ("/v1/models", "/models"):
                self._json(200, {"object": "list", "data": [
                    {"id": r["model"], "object": "model", "owned_by": "forge-router"} for r in routes.values()]})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?")[0] not in ("/v1/chat/completions", "/chat/completions"):
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            route = routes[classify(self.headers.get("X-Forge-Task"))]
            try:
                body = rewrite_body(self.rfile.read(length), route["model"])
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            parts = urlsplit(route["base"])
            conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(parts.hostname, parts.port, timeout=300)
            try:
                conn.request("POST", parts.path + "/chat/completions", body, upstream_headers(route, len(body), self.headers))
                resp = conn.getresponse()
            except OSError as exc:
                return self._json(502, {"error": "upstream unreachable: %s" % type(exc).__name__})
            self.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                chunk = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            conn.close()

    return Handler


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the stdlib default of 5 drops a 30-client burst into SYN retries (~1 s)


def main():
    routes = load_routes(os.environ)
    port = int(os.environ.get("PORT", "4000"))
    Server(("0.0.0.0", port), make_handler(routes)).serve_forever()


if __name__ == "__main__":
    main()
