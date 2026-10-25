#!/usr/bin/env python3
"""overlay-check.py: validate a private-distribution overlay directory against the forge contract.

Usage: python3 -I scripts/overlay-check.py <overlay-dir>

The contract is docs/overlay-contract.md. The upstream and registry rules are not restated here:
the overlay's files are loaded by forge's own `load_upstreams` and `load_registry`, so a change to
either loader changes what an overlay may be. Stdlib only. Output names file and rule, never a
value; exit 0 valid, 1 violations, 2 usage or an unreadable input.
"""
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = re.compile(r"[a-z0-9-]+")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
SECRET_NAME = re.compile(r"(_TOKEN|_SECRET|_PASSWORD)$")
PLACEHOLDER = re.compile(r"<[^<>]+>|change-me.*|stub-token")
REQUIRED_UPSTREAMS = ("models-default", "models-escalation")
CONTRACT = 1


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relative))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path, errors):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        errors.append("%s: missing" % os.path.relpath(path))
    except (OSError, ValueError) as exc:
        errors.append("%s: unreadable JSON (%s)" % (os.path.relpath(path), type(exc).__name__))
    return None


def check_policy_params(override, errors):
    """The policy slot is validated by the pack's own validator, merged over its defaults."""
    if not isinstance(override, dict):
        errors.append("policy/params.json: must be a JSON object")
        return
    pack = load_module("forge_policy_params", "policy/forge_policies/params.py")
    merged = pack.merge(pack.load_defaults(), override)
    for message in pack.validate(merged):
        errors.append("policy/params.json: " + message)


def forge_variables():
    """Variable names .env.example sets or shows as a commented placeholder."""
    names = set()
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"#?\s*([A-Z][A-Z0-9_]*)=", line)
            if match:
                names.add(match.group(1))
    return names


def check_env(path, allowed, errors):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        errors.append("overlay.env: missing")
        return
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        key, sep, value = text.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        where = "overlay.env:%d" % number
        if not sep or not ENV_NAME.fullmatch(key):
            errors.append("%s: not a KEY=VALUE line" % where)
        elif not (key in allowed or key.startswith("OVERLAY_")):
            errors.append("%s: %s is not a forge variable, an upstream variable or OVERLAY_-prefixed" % (where, key))
        elif SECRET_NAME.search(key) and value and not PLACEHOLDER.fullmatch(value):
            errors.append("%s: %s carries a value; credentials come from the shell, never a committed file" % (where, key))


def check_upstreams(overlay, errors):
    path = os.path.join(overlay, "upstreams.json")
    spec = read_json(path, errors)
    if not isinstance(spec, dict):
        return None, set()
    egress = load_module("forge_egress", "server/egress/egress.py")
    try:
        egress.load_upstreams(path, {})
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append("upstreams.json: refused by the gateway loader (%s)" % exc)
    names = set()
    for upstream, entry in spec.items():
        if not NAME.fullmatch(upstream):
            errors.append("upstreams.json: upstream name %r is not [a-z0-9-]+" % upstream)
        for field in ("base_url_env", "token_env"):
            value = entry.get(field) if isinstance(entry, dict) else None
            if isinstance(value, str) and ENV_NAME.fullmatch(value):
                names.add(value)
            else:
                errors.append("upstreams.json: %s.%s must name an environment variable" % (upstream, field))
    for required in REQUIRED_UPSTREAMS:
        if required not in spec:
            errors.append("upstreams.json: the %s upstream is required (the router depends on it)" % required)
    return path, names


def check_agents(overlay, errors):
    declared = set()
    folder = os.path.join(overlay, "agents")
    if not os.path.isdir(folder):
        return declared
    for file in sorted(os.listdir(folder)):
        if not file.endswith(".json"):
            continue
        agent = read_json(os.path.join(folder, file), errors)
        if agent is None:
            continue
        label = "agents/%s" % file
        if not (isinstance(agent, dict) and isinstance(agent.get("name"), str) and NAME.fullmatch(agent["name"])):
            errors.append("%s: needs a name matching [a-z0-9-]+" % label)
        elif agent["name"] in declared:
            errors.append("%s: role %s is declared twice" % (label, agent["name"]))
        else:
            declared.add(agent["name"])
        if isinstance(agent, dict) and not isinstance(agent.get("description"), str):
            errors.append("%s: needs a description" % label)
    return declared


def check_registry(overlay, upstreams_path, roles, errors):
    path = os.path.join(overlay, "registry.json")
    if read_json(path, errors) is None or upstreams_path is None:
        return
    registry = load_module("forge_registry", "server/mcp/registry.py")
    try:
        servers = registry.load_registry(path, upstreams_path)
    except (ValueError, KeyError, TypeError) as exc:
        errors.append("registry.json: refused by the registry loader (%s)" % exc)
        return
    for entry in servers:
        for role in entry["roles"]:
            if role not in roles:
                errors.append("registry.json: %s lists role %s, which no agents/*.json declares" % (entry["name"], role))


def check(overlay):
    errors = []
    manifest = read_json(os.path.join(overlay, "overlay.json"), errors)
    if manifest is not None:
        if not isinstance(manifest, dict) or manifest.get("contract") != CONTRACT:
            errors.append("overlay.json: contract must be %d" % CONTRACT)
        elif not (isinstance(manifest.get("name"), str) and NAME.fullmatch(manifest["name"])):
            errors.append("overlay.json: name must match [a-z0-9-]+")
    if not os.path.isfile(os.path.join(overlay, "compose.overlay.yaml")):
        errors.append("compose.overlay.yaml: missing")
    upstreams_path, upstream_vars = check_upstreams(overlay, errors)
    check_env(os.path.join(overlay, "overlay.env"), forge_variables() | upstream_vars, errors)
    roles = check_agents(overlay, errors)
    check_registry(overlay, upstreams_path, roles, errors)
    params = os.path.join(overlay, "policy", "params.json")
    if os.path.exists(params):
        override = read_json(params, errors)
        if override is not None:
            check_policy_params(override, errors)
    return errors


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: overlay-check.py <overlay-dir>", file=sys.stderr)
        return 2
    os.chdir(argv[1])
    errors = check(".")
    for message in errors:
        print("overlay-check: " + message, file=sys.stderr)
    if errors:
        return 1
    print("overlay-check: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
