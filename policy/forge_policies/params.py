"""Parameters of the forge policy pack: defaults, merge, validation and rendering.

Used offline (renderer, overlay checker, gate); the server never imports it. Stdlib only.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_PATH = os.path.join(os.path.dirname(HERE), "params.default.json")

MODULE = "forge_policies"
COST = "omnigent.policies.builtins.cost"
SAFETY = "omnigent.policies.builtins.safety"

OUTPUT_CATEGORIES = (
    "private_key", "dapi_token", "cloud_key", "vcs_token", "jwt",
    "cpf", "cnpj", "credit_card", "ssn", "email", "phone",
)
OUTPUT_PHASES = ("response", "tool_result")
ACTIONS = ("ASK", "DENY")

_STR_LISTS = {
    "approval": ("commands", "tools"),
    "egress": ("allow_hosts", "deny_hosts", "network_tools", "shell_network_commands"),
    "output": ("detect", "extra_patterns", "phases"),
}
_SCHEMA_KEYS = {
    "approval": {"commands", "tools", "action"},
    "budget": {"session_max_usd", "session_ask_usd", "daily_max_usd", "daily_ask_usd",
               "expensive_models", "max_tool_calls"},
    "egress": {"allow_hosts", "deny_hosts", "network_tools", "shell_network_commands",
               "action_unparsed"},
    "output": {"detect", "extra_patterns", "action", "phases"},
}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_defaults():
    return load_json(DEFAULTS_PATH)


def merge(defaults, override):
    """Group by group, key by key; a leaf (list, scalar, null) in the override replaces whole."""
    merged = {group: dict(values) for group, values in defaults.items()}
    for group, values in (override or {}).items():
        if isinstance(values, dict) and group in merged:
            merged[group].update(values)
        else:
            merged[group] = values
    return merged


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num_or_null(errors, where, value, positive=True):
    if value is None:
        return
    if not _is_num(value) or (positive and value <= 0):
        errors.append(f"{where}: must be a positive number or null")


def _num_list(errors, where, value):
    if not isinstance(value, list) or not all(_is_num(v) and v > 0 for v in value):
        errors.append(f"{where}: must be a list of positive numbers")
        return False
    if value != sorted(set(value)):
        errors.append(f"{where}: must be strictly increasing")
        return False
    return True


def _check_pair(errors, group, key_max, key_ask, params):
    maximum, asks = params.get(key_max), params.get(key_ask)
    if isinstance(asks, list) and asks:
        if maximum is None:
            errors.append(f"{group}.{key_ask}: needs {key_max}")
        elif _is_num(maximum) and any(_is_num(a) and a >= maximum for a in asks):
            errors.append(f"{group}.{key_ask}: every threshold must be below {key_max}")


def validate(params):
    """Return a list of error strings; empty means valid."""
    errors = []
    if not isinstance(params, dict):
        return ["parameters: must be a JSON object"]
    for group in params:
        if group not in _SCHEMA_KEYS:
            errors.append(f"{group}: unknown group")
    for group, allowed in _SCHEMA_KEYS.items():
        values = params.get(group)
        if not isinstance(values, dict):
            errors.append(f"{group}: must be an object")
            continue
        for key in values:
            if key not in allowed:
                errors.append(f"{group}.{key}: unknown key")
        for key in _STR_LISTS.get(group, ()):
            value = values.get(key)
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                errors.append(f"{group}.{key}: must be a list of non-empty strings")
        for key in ("action", "action_unparsed"):
            if key in values and values[key] not in ACTIONS:
                errors.append(f"{group}.{key}: must be one of {', '.join(ACTIONS)}")
    out = params.get("output")
    if isinstance(out, dict):
        for name in out.get("detect", []) if isinstance(out.get("detect"), list) else []:
            if name not in OUTPUT_CATEGORIES:
                errors.append(f"output.detect: unknown category {name!r}")
        for name in out.get("phases", []) if isinstance(out.get("phases"), list) else []:
            if name not in OUTPUT_PHASES:
                errors.append(f"output.phases: unknown phase {name!r}")
        for pattern in out.get("extra_patterns", []) if isinstance(out.get("extra_patterns"), list) else []:
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                errors.append("output.extra_patterns: an entry is not a valid regular expression")
    bud = params.get("budget")
    if isinstance(bud, dict):
        _num_or_null(errors, "budget.session_max_usd", bud.get("session_max_usd"))
        _num_or_null(errors, "budget.daily_max_usd", bud.get("daily_max_usd"))
        ints = bud.get("max_tool_calls")
        if ints is not None and (not isinstance(ints, int) or isinstance(ints, bool) or ints <= 0):
            errors.append("budget.max_tool_calls: must be a positive integer or null")
        for key in ("session_ask_usd", "daily_ask_usd"):
            _num_list(errors, f"budget.{key}", bud.get(key))
        models = bud.get("expensive_models")
        if models is not None and (not isinstance(models, list)
                                   or not all(isinstance(m, str) and m for m in models)):
            errors.append("budget.expensive_models: must be a list of strings or null")
        _check_pair(errors, "budget", "session_max_usd", "session_ask_usd", bud)
        _check_pair(errors, "budget", "daily_max_usd", "daily_ask_usd", bud)
    return errors


def _entry(handler, factory_params):
    return {"type": "function", "handler": handler, "factory_params": factory_params}


def render(params):
    """Build the Omnigent server config (a dict) from validated parameters."""
    errors = validate(params)
    if errors:
        raise ValueError("; ".join(errors))
    policies = {
        "forge__egress": _entry(f"{MODULE}.egress_guard", dict(params["egress"])),
        "forge__approval": _entry(f"{MODULE}.approval_gate", dict(params["approval"])),
    }
    bud = params["budget"]
    if bud.get("session_max_usd") is not None:
        fp = {"max_cost_usd": bud["session_max_usd"]}
        if bud.get("session_ask_usd"):
            fp["ask_thresholds_usd"] = list(bud["session_ask_usd"])
        if bud.get("expensive_models") is not None:
            fp["expensive_models"] = list(bud["expensive_models"])
        policies["forge__session_budget"] = _entry(f"{COST}.cost_budget", fp)
    if bud.get("daily_max_usd") is not None:
        fp = {"max_cost_usd": bud["daily_max_usd"]}
        if bud.get("daily_ask_usd"):
            fp["ask_thresholds_usd"] = list(bud["daily_ask_usd"])
        if bud.get("expensive_models") is not None:
            fp["expensive_models"] = list(bud["expensive_models"])
        policies["forge__daily_budget"] = _entry(f"{COST}.user_daily_cost_budget", fp)
    if bud.get("max_tool_calls") is not None:
        policies["forge__tool_call_limit"] = _entry(
            f"{SAFETY}.max_tool_calls_per_session", {"limit": bud["max_tool_calls"]})
    policies["forge__output"] = _entry(f"{MODULE}.output_guard", dict(params["output"]))
    return {"policy_modules": [MODULE], "policies": policies}


def render_text(params):
    return json.dumps(render(params), indent=2, sort_keys=False) + "\n"
