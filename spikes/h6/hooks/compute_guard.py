#!/usr/bin/env python3
"""Synthetic baseline hook (Claude Code PreToolUse): refuse compute outside an approved cluster.

Reads the hook event on stdin, the approved list from the registry file. Exit 2 blocks and the
stderr line is the reason. Written the way such hooks usually are: regular expressions over the
raw command text. Every name here is synthetic.
"""
import json
import os
import re
import sys

REGISTRY = os.environ.get("H6_REGISTRY") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "registry.synthetic.json")
CLUSTER_KEYS = ("cluster_id", "existing_cluster_id", "cluster")
FLAG = re.compile(r"--(?:cluster-id|existing-cluster-id|cluster)(?:=|\s+)(\S+)")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def main():
    event = json.load(sys.stdin)
    with open(REGISTRY, encoding="utf-8") as fh:
        approved = set(json.load(fh)["approved_clusters"])
    tool_input = event.get("tool_input") or {}
    named = []
    command = tool_input.get("command")
    if isinstance(command, str):
        if not re.search(r"\bsynthcli\s+compute\b", command):
            return
        if re.search(r"\bsynthcli\s+compute\s+create\b", command):
            block("compute create is not allowed; use an approved cluster")
        named = [m.strip("'\"") for m in FLAG.findall(command)]
    else:
        named = [str(tool_input[k]) for k in CLUSTER_KEYS if k in tool_input]
    for cluster in named:
        if cluster not in approved:
            block("cluster is not in the approved list")


main()
