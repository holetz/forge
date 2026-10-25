"""forge egress gateway: the one place an upstream token lives.

Stdlib only. A client addresses http://egress:3128/u/<upstream>/<path>; <upstream> must be a key of
the allowlist file. The gateway forwards only an allowlist of client headers (so no client
credential, cookie or key reaches an upstream), injects the upstream's own, forwards
over the upstream's scheme and streams the response back. Anything else is refused. One JSON audit
line per request goes to the audit file: never a credential, a token or a body. The audit identity
(`user`, `session`) is recorded only when the router signed it (X-Forge-Identity, an HMAC with a
secret shared with the router alone); any other request is recorded as `unauthenticated`, whatever
it claims. The signature attests the router, not the user: the router signs the name a runner gave
it, so a recorded user is a claim the router vouched for (docs/audit-identity.md).
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import posixpath
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
              "proxy-authenticate", "proxy-authorization", "proxy-connection"}
FORWARD_HEADERS = frozenset({
    "accept", "accept-encoding", "accept-language", "cache-control", "content-encoding", "content-type",
    "git-protocol", "if-modified-since", "if-none-match", "last-event-id", "mcp-protocol-version",
    "mcp-session-id", "pragma", "range", "user-agent"})
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
CAPTURE_LIMIT = 1 << 20
CONTROL = re.compile(r"[^\x20-\x7e]")
IDENTITY_HEADER = "X-Forge-Identity"
IDENTITY_MAX_SKEW = 300
IDENTITY_MIN_SECRET = 32
UNAUTHENTICATED = "unauthenticated"
ENCODED_SEPARATOR = re.compile(r"%(2f|5c|00)", re.IGNORECASE)
DECODE_ROUNDS = 3


def load_upstreams(path, env, plain_http_hosts=("stub",)):
    """Read the allowlist; resolve each upstream from the environment. An unset base URL disables it."""
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    upstreams = {}
    for name, entry in spec.items():
        base = env.get(entry["base_url_env"], "").strip().rstrip("/")
        token = env.get(entry["token_env"], "").strip()
        if base:
            parts = urlsplit(base)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("upstream %s: invalid base url" % name)
            if parts.scheme == "http" and parts.hostname not in plain_http_hosts:
                raise ValueError("upstream %s: plain http refused (only %s may use it)" % (name, ", ".join(plain_http_hosts)))
            if not token:
                raise ValueError("upstream %s: base url set but no token" % name)
        upstreams[name] = {"base": base, "token": token, "auth": entry.get("auth", "bearer"),
                           "record_model": bool(entry.get("record_model")), "allow": parse_allow(name, entry.get("allow"))}
    return upstreams


def parse_allow(name, allow):
    """Validate an upstream's method and path-prefix rules; an upstream with none fails to load."""
    if not isinstance(allow, list) or not allow:
        raise ValueError("upstream %s: no allow rules (methods and prefixes are required)" % name)
    rules = []
    for rule in allow:
        rule = rule if isinstance(rule, dict) else {}
        methods, prefixes = rule.get("methods"), rule.get("prefixes")
        if not (isinstance(methods, list) and methods and all(m in METHODS for m in methods)):
            raise ValueError("upstream %s: allow rule needs methods from %s" % (name, ", ".join(METHODS)))
        if not (isinstance(prefixes, list) and prefixes and all(isinstance(p, str) and p.startswith("/") for p in prefixes)):
            raise ValueError("upstream %s: allow rule needs path prefixes starting with /" % name)
        rules.append({"methods": frozenset(methods), "prefixes": tuple(prefixes)})
    return rules


def credential(upstream):
    if upstream["auth"] == "basic":
        return "Basic " + base64.b64encode(("forge:" + upstream["token"]).encode()).decode()
    return "Bearer " + upstream["token"]


def split_target(path):
    """/u/<name>/<rest>?<query> -> (name, '/<rest>', query); None when the shape is wrong."""
    raw, _, query = path.partition("?")
    match = re.fullmatch(r"/u/([a-z0-9-]+)(/.*)?", raw)
    if not match:
        return None
    return match.group(1), match.group(2) or "/", query


def safe_rest(rest):
    """True when `rest` has no dot segment, empty inner segment, separator or control byte.

    The check runs on the raw text and on each percent-decoding of it (up to DECODE_ROUNDS), so
    `%2e%2e`, `%252e%252e` and `..;x` are refused. The raw text is what gets forwarded: what was
    checked is what is sent.
    """
    current = rest
    for _ in range(DECODE_ROUNDS + 1):
        if "#" in current or "\\" in current or CONTROL.search(current) or ENCODED_SEPARATOR.search(current):
            return False
        segments = current.split("/")[1:]
        for index, segment in enumerate(segments):
            if segment.split(";")[0] in (".", "..") or (segment == "" and index < len(segments) - 1):
                return False
        decoded = unquote(current)
        if decoded == current:
            return True
        current = decoded
    return False


def under_base(base_path, rest):
    """True when base_path + rest, normalised, still sits at or under base_path."""
    base = base_path.rstrip("/")
    joined = posixpath.normpath(base + rest)
    return joined == (base or "/") or joined.startswith(base + "/")


def permitted(upstream, method, rest):
    """True when one allow rule covers the method and a path prefix (matched at a segment boundary)."""
    for rule in upstream["allow"]:
        if method in rule["methods"] and any(
                p == "/" or rest == p or rest.startswith(p.rstrip("/") + "/") for p in rule["prefixes"]):
            return True
    return False


def sign_identity(secret, user, session, now):
    """X-Forge-Identity value: v1.<unix ts>.<HMAC-SHA256(secret, v1 ts user session)>. Router and gateway agree on it."""
    stamp = str(int(now))
    message = "v1\n%s\n%s\n%s" % (stamp, user or "", session or "")
    return "v1.%s.%s" % (stamp, hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest())


def verify_identity(secret, user, session, header, now):
    """True only for a fresh, well-formed signature over exactly this user and session."""
    parts = (header or "").split(".")
    if not secret or len(parts) != 3 or parts[0] != "v1" or not re.fullmatch(r"[0-9]{1,12}", parts[1]):
        return False
    if abs(now - int(parts[1])) > IDENTITY_MAX_SKEW:
        return False
    expected = sign_identity(secret, user, session, int(parts[1]))
    return hmac.compare_digest(expected.encode(), header.encode("utf-8", "replace"))


def identity_secret(env):
    """The shared secret from EGRESS_IDENTITY_SECRET; unset disables verification (all unauthenticated)."""
    secret = env.get("EGRESS_IDENTITY_SECRET", "").strip()
    if secret and len(secret) < IDENTITY_MIN_SECRET:
        raise ValueError("EGRESS_IDENTITY_SECRET must be at least %d characters" % IDENTITY_MIN_SECRET)
    return secret or None


def clean(value):
    return CONTROL.sub("", value or "")[:128] or None


class Audit:
    def __init__(self, path, max_bytes):
        self.path, self.max_bytes, self.lock = path, max_bytes, threading.Lock()

    def write(self, record):
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self.lock:
            if os.path.exists(self.path) and os.path.getsize(self.path) + len(line) > self.max_bytes:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)


def usage_of(body):
    try:
        usage = json.loads(body).get("usage") or {}
    except (ValueError, AttributeError):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {k: usage[k] for k in keys if isinstance(usage.get(k), int)} or None


def model_of(raw):
    try:
        model = json.loads(raw).get("model")
    except (ValueError, AttributeError):
        return None
    return clean(model) if isinstance(model, str) else None


def make_handler(upstreams, audit, secret=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("egress: " + (fmt % args) + "\n")

        def _reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            if code >= 400:
                self.close_connection = True  # the request body was not read: never parse it as the next request
            return data

        def do_CONNECT(self):
            self._reply(405, {"error": "CONNECT is not supported: use /u/<upstream>/"})
            self._record("-", "CONNECT", "-", 405, 0, 0, time.monotonic())

        def _record(self, upstream, method, path, status, bytes_in, bytes_out, started, extra=None):
            claimed_user, claimed_session = self.headers.get("X-Forge-User"), self.headers.get("X-Forge-Session")
            verified = verify_identity(secret, claimed_user, claimed_session, self.headers.get(IDENTITY_HEADER), time.time())
            record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "id": uuid.uuid4().hex[:12],
                      "upstream": upstream, "method": method, "path": path, "status": status,
                      "bytes_in": bytes_in, "bytes_out": bytes_out,
                      "ms": int((time.monotonic() - started) * 1000),
                      "user": clean(claimed_user) if verified else UNAUTHENTICATED,
                      "session": clean(claimed_session) if verified else UNAUTHENTICATED,
                      "identity": "router" if verified else UNAUTHENTICATED}
            record.update(extra or {})
            audit.write(record)

        def _proxy(self):
            started = time.monotonic()
            path_only = self.path.split("?")[0]
            if path_only == "/healthz":
                return self._reply(200, {"status": "ok"})
            if self.headers.get("Transfer-Encoding"):
                self._reply(411, {"error": "send Content-Length"})
                return self._record("-", self.command, path_only, 411, 0, 0, started)
            target = split_target(self.path)
            upstream = upstreams.get(target[0]) if target else None
            name = target[0] if target else "-"
            if upstream is None:
                self._reply(403, {"error": "upstream not on the allowlist"})
                return self._record(name, self.command, path_only, 403, 0, 0, started)
            if not safe_rest(target[1]):
                self._reply(400, {"error": "path refused: dot segment, encoded separator or control byte"})
                return self._record(name, self.command, path_only, 400, 0, 0, started)
            if not permitted(upstream, self.command, target[1]):
                self._reply(403, {"error": "method or path not allowed for this upstream"})
                return self._record(name, self.command, path_only, 403, 0, 0, started)
            if not upstream["base"]:
                self._reply(503, {"error": "upstream not configured"})
                return self._record(name, self.command, path_only, 503, 0, 0, started)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            extra = {"model": model_of(raw)} if upstream["record_model"] and raw else {}
            parts = urlsplit(upstream["base"])
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_HEADERS}
            headers["Authorization"] = credential(upstream)
            headers["Content-Length"] = str(len(raw))
            if not under_base(parts.path, target[1]):
                self._reply(400, {"error": "path escapes the upstream base"})
                return self._record(name, self.command, path_only, 400, len(raw), 0, started, extra)
            url = parts.path + target[1] + ("?" + target[2] if target[2] else "")
            conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(parts.hostname, parts.port, timeout=300)
            try:
                conn.request(self.command, url, raw, headers)
                resp = conn.getresponse()
            except OSError as exc:
                self._reply(502, {"error": "upstream unreachable: %s" % type(exc).__name__})
                return self._record(name, self.command, path_only, 502, len(raw), 0, started, extra)
            self.send_response(resp.status)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sent, kept = 0, bytearray()
            is_json = "application/json" in (resp.getheader("Content-Type") or "")
            while True:
                chunk = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
                if is_json and len(kept) < CAPTURE_LIMIT:
                    kept += chunk
            conn.close()
            if upstream["record_model"] and is_json and sent <= CAPTURE_LIMIT:
                extra["usage"] = usage_of(bytes(kept))
            self._record(name, self.command, path_only, resp.status, len(raw), sent, started, extra)

    for method in METHODS:
        setattr(Handler, "do_" + method, Handler._proxy)
    return Handler


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the stdlib default of 5 drops a 30-client burst into SYN retries (~1 s)


def main():
    env = os.environ
    hosts = tuple(h for h in env.get("EGRESS_PLAIN_HTTP_HOSTS", "stub").split(",") if h)
    upstreams = load_upstreams(env.get("EGRESS_UPSTREAMS_FILE", "/app/upstreams.json"), env, hosts)
    audit = Audit(env.get("EGRESS_AUDIT_PATH", "/audit/egress.jsonl"), int(env.get("EGRESS_AUDIT_MAX_BYTES", str(50 << 20))))
    port = int(env.get("PORT", "3128"))
    Server(("0.0.0.0", port), make_handler(upstreams, audit, identity_secret(env))).serve_forever()


if __name__ == "__main__":
    main()
