"""egress_guard: block or ask on network hosts outside the allowlist. Stdlib only."""
import os
import re
import shlex
from urllib.parse import urlsplit

from forge_policies.shellwords import expand

SHELL_TOOLS = frozenset({"sys_os_shell", "Bash", "bash", "Shell", "terminal", "developer__shell", "shell"})
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_USER_HOST = re.compile(r"^(?:[^@\s/]+@)([A-Za-z0-9.\-]+|\[[0-9A-Fa-f:]+\]):")
_HOSTLIKE = re.compile(r"^(?:[^@\s/]+@)?(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9][A-Za-z0-9.\-]*)(?::\d+)?(?:[:/].*)?$")
_URL_KEYS = ("url", "uri", "href")
# options whose value is a file, header or payload, never a connection target
_VALUE_OPTS = frozenset({"-o", "-O", "-d", "-H", "-A", "-u", "-F", "-T", "--output", "--data", "--header",
                         "--user-agent", "--user", "--form", "--upload-file", "--output-document"})


def _host_of(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _norm(host):
    return host.strip().strip("[]").lower().rstrip(".")


def _matches(host, entry):
    entry = _norm(entry)
    return host == entry or host.endswith("." + entry)


def _segment_hosts(segment, commands):
    """Return (hosts, has_network_command, unparsed)."""
    hosts = {_host_of(u) for u in _URL.findall(segment)}
    hosts.discard("")
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    heads = [i for i, t in enumerate(tokens) if os.path.basename(t) in commands]
    found = bool(heads)
    for t in tokens:
        m = _USER_HOST.match(t)
        if m and heads:
            hosts.add(_norm(m.group(1)))
    if heads:
        skip = False
        for t in tokens[heads[0] + 1:]:
            if skip:
                skip = False
                continue
            if t in _VALUE_OPTS:
                skip = True
                continue
            if t.startswith("-") or "=" in t.split("/")[0]:
                continue
            m = _HOSTLIKE.match(t)
            if m and ("." in m.group(1) or m.group(1).lower() == "localhost" or ":" in m.group(1)):
                hosts.add(_norm(m.group(1)))
    return {_norm(h) for h in hosts}, found, found and not hosts


def egress_guard(allow_hosts=(), deny_hosts=(), network_tools=(), shell_network_commands=(),
                 action_unparsed="ASK"):
    allow, deny = [h for h in allow_hosts], [h for h in deny_hosts]
    net_tools, commands = set(network_tools), set(shell_network_commands)
    unparsed = "DENY" if action_unparsed == "DENY" else "ASK"

    def judge(hosts):
        for host in sorted(hosts):
            if any(_matches(host, d) for d in deny):
                return {"result": "DENY", "reason": f"egress to {host} is blocked"}
        if allow:
            for host in sorted(hosts):
                if not any(_matches(host, a) for a in allow):
                    return {"result": "DENY", "reason": f"egress to {host} is not on the allowlist"}
        return None

    def evaluate(event):
        if event.get("type") != "tool_call":
            return None
        data = event.get("data") or {}
        name = event.get("target") or data.get("name") or ""
        args = data.get("arguments") or {}
        if name in net_tools:
            hosts = {_host_of(str(args[k])) for k in _URL_KEYS if k in args}
            hosts.discard("")
            if not hosts:
                return {"result": unparsed, "reason": f"{name} call without a parsable host"}
            return judge(hosts)
        if name in SHELL_TOOLS:
            command = args.get("command") or args.get("cmd") or ""
            if isinstance(command, list):
                command = " ".join(str(c) for c in command)
            segments, parsed = expand(str(command))
            hosts, blind = set(), False
            for seg in segments:
                h, _found, miss = _segment_hosts(seg, commands)
                hosts |= h
                blind = blind or miss
            if not parsed and any(re.search(r"\b%s\b" % re.escape(c), str(command)) for c in commands):
                blind = True
            verdict = judge(hosts) if hosts else None
            if verdict:
                return verdict
            if blind:
                return {"result": unparsed, "reason": "network command without a parsable host"}
        return None

    return evaluate
