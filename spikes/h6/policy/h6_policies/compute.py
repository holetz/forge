"""compute_guard: refuse compute aimed outside an approved cluster. Stdlib only, synthetic CLI."""
import os
import re
import shlex

SHELL_TOOLS = frozenset({"sys_os_shell", "Bash", "bash", "Shell", "terminal", "developer__shell", "shell"})
_SPLIT = re.compile(r"[;&|\n]+")


def segments(command):
    """Yield (tokens, parsed) per command segment; an unbalanced quote falls back to a plain split."""
    for segment in _SPLIT.split(command):
        if not segment.strip():
            continue
        try:
            yield shlex.split(segment), True
        except ValueError:
            yield segment.split(), False


def after_head(tokens, head, sub=None):
    """Tokens following the first token whose basename is `head` (and, when given, `sub`), else None."""
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) != head:
            continue
        rest = tokens[i + 1:]
        if sub is None:
            return rest
        if rest and rest[0] == sub:
            return rest[1:]
    return None


def flag_values(tokens, flags):
    """Values of every `--flag value` and `--flag=value`; None stands for a flag with no value."""
    found = []
    for i, tok in enumerate(tokens):
        for flag in flags:
            if tok == flag:
                found.append(tokens[i + 1] if i + 1 < len(tokens) else None)
            elif tok.startswith(flag + "="):
                found.append(tok[len(flag) + 1:])
    return found


def event_parts(event):
    name = event.get("target") or (event.get("data") or {}).get("name") or ""
    args = (event.get("data") or {}).get("arguments") or {}
    return name, args


def command_of(args):
    command = args.get("command") or args.get("cmd") or ""
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    return str(command)


def compute_guard(approved_clusters=(), cluster_flags=("--cluster-id", "--existing-cluster-id", "--cluster"),
                  cluster_keys=("cluster_id", "existing_cluster_id", "cluster")):
    approved = set(approved_clusters)

    def deny(reason):
        return {"result": "DENY", "reason": reason}

    def evaluate(event):
        if event.get("type") != "tool_call":
            return None
        name, args = event_parts(event)
        if name in SHELL_TOOLS:
            for tokens, parsed in segments(command_of(args)):
                if not parsed:
                    if "synthcli compute" in " ".join(tokens):
                        return deny("compute command could not be parsed")
                    continue
                rest = after_head(tokens, "synthcli", "compute")
                if rest is None:
                    continue
                if rest and rest[0] == "create":
                    return deny("compute create is not allowed; use an approved cluster")
                for value in flag_values(rest, cluster_flags):
                    if value is None or value not in approved:
                        return deny("cluster is not in the approved list")
            return None
        for key in cluster_keys:
            if key in args and str(args[key]) not in approved:
                return deny("cluster is not in the approved list")
        return None

    return evaluate
