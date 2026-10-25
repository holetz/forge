"""approval_gate: ask (or deny) before a push, deploy or publish. Stdlib only."""
import fnmatch
import os
import re
import shlex

SHELL_TOOLS = frozenset({"sys_os_shell", "Bash", "bash", "Shell", "terminal", "developer__shell", "shell"})
_SPLIT = re.compile(r"[;&|\n]+")
# options that consume the next token, per head command (skipped when matching a sequence)
_VALUE_OPTS = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "kubectl": {"-n", "--namespace", "--context", "--kubeconfig", "-f", "-s", "--server"},
    "helm": {"-n", "--namespace", "--kube-context", "--kubeconfig"},
    "gh": {"-R", "--repo"},
    "docker": {"-H", "--host", "-c", "--context"},
    "databricks": {"-p", "--profile", "-t", "--target"},
    "terraform": {"-chdir"},
}


def _tokens(segment):
    try:
        return shlex.split(segment), True
    except ValueError:
        return segment.split(), False


def _matches(tokens, sequence):
    """True when some token's basename is sequence[0] and the rest follow as non-option tokens."""
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) != sequence[0]:
            continue
        skip_opts = _VALUE_OPTS.get(sequence[0], set())
        rest, j, ok = list(sequence[1:]), i + 1, True
        for word in rest:
            while j < len(tokens) and tokens[j].startswith("-"):
                j += 2 if tokens[j] in skip_opts else 1
            if j >= len(tokens) or tokens[j] != word:
                ok = False
                break
            j += 1
        if ok:
            return True
    return False


def _shell_hit(command, sequences):
    for segment in _SPLIT.split(command):
        if not segment.strip():
            continue
        tokens, parsed = _tokens(segment)
        for seq in sequences:
            if _matches(tokens, seq):
                return " ".join(seq)
            if not parsed and seq[0] in segment:
                return " ".join(seq)
    return None


def approval_gate(commands=(), tools=(), action="ASK"):
    sequences = [c.split() for c in commands if c.split()]
    patterns = list(tools)
    verdict = "DENY" if action == "DENY" else "ASK"

    def evaluate(event):
        if event.get("type") != "tool_call":
            return None
        name = event.get("target") or (event.get("data") or {}).get("name") or ""
        if any(fnmatch.fnmatchcase(name, p) for p in patterns):
            return {"result": verdict, "reason": f"approval required for tool {name}"}
        if name in SHELL_TOOLS:
            args = (event.get("data") or {}).get("arguments") or {}
            command = args.get("command") or args.get("cmd") or ""
            if isinstance(command, list):
                command = " ".join(str(c) for c in command)
            hit = _shell_hit(str(command), sequences) if command else None
            if hit:
                return {"result": verdict, "reason": f"approval required for: {hit}"}
        return None

    return evaluate
