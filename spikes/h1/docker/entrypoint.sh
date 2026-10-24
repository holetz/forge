#!/bin/sh
# One agent invocation: install the port into a fresh HOME, move the token into a file the
# provider reads, drop it from the environment, then run `opencode run --format json` through a
# filter that prints the transcript as plain text on stdout (what the grader reads) and the
# summed step tokens as one `H1-USAGE {json}` line on stderr (what report.py reads).
set -eu
export HOME="${HOME:-/tmp/h1home}"
mkdir -p "$HOME/.config/opencode"
cp -R /opt/opencode/. "$HOME/.config/opencode/"
umask 077
printf '%s' "${DATABRICKS_TOKEN:?DATABRICKS_TOKEN is not set}" > "$HOME/.config/opencode/.token"
unset DATABRICKS_TOKEN
umask 022
exec python3 -c '
import json, subprocess, sys
proc = subprocess.Popen(["opencode", "run", "--format", "json", *sys.argv[1:]],
                        stdout=subprocess.PIPE, text=True)
usage = {"steps": 0, "input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}
for line in proc.stdout:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        print(line, end="")
        continue
    part = event.get("part") or {}
    kind = event.get("type")
    if kind == "text" and part.get("text"):
        print(part["text"], flush=True)
    elif kind == "tool_use":
        state = part.get("state") or {}
        print("[tool " + str(part.get("tool")) + "] " + json.dumps(state.get("input"))[:2000])
        print("[tool output] " + str(state.get("output", state.get("error", "")))[:2000], flush=True)
    elif kind == "step_finish":
        tokens = part.get("tokens") or {}
        cache = tokens.get("cache") or {}
        usage["steps"] += 1
        for key in ("input", "output", "reasoning"):
            usage[key] += int(tokens.get(key) or 0)
        usage["cacheRead"] += int(cache.get("read") or 0)
        usage["cacheWrite"] += int(cache.get("write") or 0)
    elif kind == "error":
        print("[error] " + json.dumps(event.get("error", event))[:2000], flush=True)
code = proc.wait()
print("H1-USAGE " + json.dumps(usage), file=sys.stderr)
sys.exit(code)
' "$@"
