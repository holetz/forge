#!/usr/bin/env python3
"""One grading call, run INSIDE the disposable container (stdin -> stdout, no files, no mounts).

stdin : a chat-completions request body (JSON).
env   : DATABRICKS_HOST (workspace URL) and DATABRICKS_TOKEN, passed by name by regrade.py.
stdout: {"status", "content", "finish", "usage"} as one JSON line. Neither the host nor the token is
        ever printed; an HTTP error prints the status and the first 300 characters of the body
        with the host removed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DATABRICKS_TOKEN"]
request = urllib.request.Request(
    host + "/ai-gateway/mlflow/v1/chat/completions", data=sys.stdin.buffer.read(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    with urllib.request.urlopen(request, timeout=600) as response:
        body, status = json.load(response), response.status
except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", "replace").replace(host, "<host>").replace(token, "<token>")
    print(json.dumps({"status": exc.code, "error": detail[:300]}))
    sys.exit(1)
choice = (body.get("choices") or [{}])[0]
print(json.dumps({"status": status, "content": (choice.get("message") or {}).get("content") or "",
                  "finish": choice.get("finish_reason"), "usage": body.get("usage") or {}}))
