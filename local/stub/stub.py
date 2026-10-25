"""forge stub: a local OpenAI-compatible chat endpoint. No model, no network, no cost.

POST /v1/chat/completions answers a canned completion (SSE when `stream` is true) and remembers
what it saw; GET /_last reports it so the smoke can assert routing without reading logs.
The expected upstream credential is STUB_TOKEN; the stub reports whether it matched, never the value.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LAST = {}


def summarize(path, headers, body, expected_token):
    auth = headers.get("Authorization") or ""
    return {
        "path": path,
        "model": body.get("model"),
        "task": headers.get("X-Forge-Task"),
        "credential_ok": auth == "Bearer " + expected_token,
        "credential_present": bool(auth),
    }


def completion(model, content="stub reply"):
    return {"id": "stub-1", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}


def sse_events(model, content="stub reply"):
    chunk = {"id": "stub-1", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]}
    done = {"id": "stub-1", "object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    return [("data: %s\n\n" % json.dumps(chunk)).encode(), ("data: %s\n\n" % json.dumps(done)).encode(),
            b"data: [DONE]\n\n"]


def make_handler(expected_token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("stub: " + (fmt % args) + "\n")

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
            elif self.path == "/_last":
                self._json(200, LAST)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.endswith("/chat/completions"):
                return self._json(404, {"error": "not found"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            LAST.clear()
            LAST.update(summarize(self.path, self.headers, body, expected_token))
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                for event in sse_events(body.get("model")):
                    self.wfile.write(event)
                    self.wfile.flush()
            else:
                self._json(200, completion(body.get("model")))

    return Handler


def main():
    token = os.environ.get("STUB_TOKEN", "stub-token")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), make_handler(token)).serve_forever()


if __name__ == "__main__":
    main()
