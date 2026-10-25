#!/usr/bin/env python3
"""Synthetic baseline hook (Claude Code PreToolUse): refuse a column override outside the registry.

Exit 2 blocks. Regular expressions over the raw command text, as such hooks usually are.
Every name here is synthetic.
"""
import json
import os
import re
import sys

REGISTRY = os.environ.get("H6_REGISTRY") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "registry.synthetic.json")
FLAG = re.compile(r"--override-column(?:=|\s+)(\S+)")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def main():
    event = json.load(sys.stdin)
    with open(REGISTRY, encoding="utf-8") as fh:
        registry = {c.lower() for c in json.load(fh)["column_registry"]}
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command")
    if isinstance(command, str):
        if not re.search(r"\bsynthcli\b", command):
            return
        specs = [m.strip("'\"") for m in FLAG.findall(command)]
    else:
        value = tool_input.get("column_override")
        if value is None:
            return
        if isinstance(value, dict):
            specs = [f"{k}={v}" for k, v in value.items()]
        else:
            specs = [str(v) for v in (value if isinstance(value, list) else [value])]
    for spec in specs:
        if spec.split("=", 1)[0].lower() not in registry:
            block("column override is not in the registry")


main()
