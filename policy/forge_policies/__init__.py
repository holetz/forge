"""forge policy pack for Omnigent (server-wide policies, stdlib only).

The server lists this module in `policy_modules`; POLICY_REGISTRY makes the three handlers
discoverable. params.py is the parameter contract shared by the renderer, the overlay checker
and the tests; it is not imported here, so the server never loads it.
"""
from forge_policies.approval import approval_gate
from forge_policies.egress import egress_guard
from forge_policies.output import output_guard

__all__ = ["approval_gate", "egress_guard", "output_guard", "POLICY_REGISTRY"]

_STR_LIST = {"type": "array", "items": {"type": "string"}}
_ACTION = {"type": "string", "enum": ["ASK", "DENY"]}

POLICY_REGISTRY = [
    {
        "handler": "forge_policies.approval_gate",
        "kind": "factory",
        "name": "Approval Gate",
        "description": "Asks (or denies) before a push, deploy or publish: shell commands are "
        "matched by word sequence, MCP tools by name glob.",
        "params_schema": {
            "type": "object",
            "properties": {
                "commands": {**_STR_LIST, "description": "Word sequences such as 'git push'."},
                "tools": {**_STR_LIST, "description": "Tool-name globs."},
                "action": {**_ACTION, "description": "Verdict on a match."},
            },
        },
    },
    {
        "handler": "forge_policies.egress_guard",
        "kind": "factory",
        "name": "Egress Guard",
        "description": "Blocks network hosts outside the allowlist or on the deny list; an "
        "unparsable network command takes action_unparsed.",
        "params_schema": {
            "type": "object",
            "properties": {
                "allow_hosts": {**_STR_LIST, "description": "Allowed hosts (subdomains match)."},
                "deny_hosts": {**_STR_LIST, "description": "Always-blocked hosts."},
                "network_tools": {**_STR_LIST, "description": "Tools taking a url argument."},
                "shell_network_commands": {**_STR_LIST, "description": "Network commands in a shell."},
                "action_unparsed": {**_ACTION, "description": "Verdict without a parsable host."},
            },
        },
    },
    {
        "handler": "forge_policies.output_guard",
        "kind": "factory",
        "name": "Output Guard",
        "description": "Blocks secrets and personal data in responses and tool results; the "
        "reason names the category and a count, never the match.",
        "params_schema": {
            "type": "object",
            "properties": {
                "detect": {**_STR_LIST, "description": "Built-in category names."},
                "extra_patterns": {**_STR_LIST, "description": "Extra regular expressions."},
                "action": {**_ACTION, "description": "Verdict on a hit."},
                "phases": {**_STR_LIST, "description": "response and/or tool_result."},
            },
        },
    },
]
