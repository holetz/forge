"""column_override_guard: refuse a column override outside the registry. Stdlib only, synthetic CLI."""
from h6_policies.compute import SHELL_TOOLS, after_head, command_of, event_parts, flag_values, segments


def _specs(value):
    if isinstance(value, dict):
        return [f"{k}={v}" for k, v in value.items()]
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def column_override_guard(registry=(), flag="--override-column", key="column_override"):
    allowed = {c.lower() for c in registry}

    def deny():
        return {"result": "DENY", "reason": "column override is not in the registry"}

    def evaluate(event):
        if event.get("type") != "tool_call":
            return None
        name, args = event_parts(event)
        if name in SHELL_TOOLS:
            for tokens, parsed in segments(command_of(args)):
                if not parsed:
                    if "synthcli" in " ".join(tokens) and flag in " ".join(tokens):
                        return deny()
                    continue
                rest = after_head(tokens, "synthcli")
                if rest is None:
                    continue
                for value in flag_values(rest, (flag,)):
                    if value is None or value.split("=", 1)[0].lower() not in allowed:
                        return deny()
            return None
        if key in args:
            for spec in _specs(args[key]):
                if spec.split("=", 1)[0].lower() not in allowed:
                    return deny()
        return None

    return evaluate
