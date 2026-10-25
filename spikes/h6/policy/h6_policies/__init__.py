"""Spike H6: two hook guard patterns ported to Omnigent function policies (synthetic, stdlib only).

Listed in `policy_modules`; POLICY_REGISTRY makes the handlers discoverable, the same mechanism
as forge_policies. Imports nothing from omnigent.
"""
from h6_policies.columns import column_override_guard
from h6_policies.compute import compute_guard

__all__ = ["compute_guard", "column_override_guard", "POLICY_REGISTRY"]

_STR_LIST = {"type": "array", "items": {"type": "string"}}

POLICY_REGISTRY = [
    {
        "handler": "h6_policies.compute_guard",
        "kind": "factory",
        "name": "Compute Guard",
        "description": "Denies compute aimed at a cluster outside the approved list, and any compute create.",
        "params_schema": {
            "type": "object",
            "properties": {
                "approved_clusters": {**_STR_LIST, "description": "Cluster ids that may be used."},
                "cluster_flags": {**_STR_LIST, "description": "CLI flags that name a cluster."},
                "cluster_keys": {**_STR_LIST, "description": "Tool argument keys that name a cluster."},
            },
        },
    },
    {
        "handler": "h6_policies.column_override_guard",
        "kind": "factory",
        "name": "Column Override Guard",
        "description": "Denies a column override whose table.column is not in the registry.",
        "params_schema": {
            "type": "object",
            "properties": {
                "registry": {**_STR_LIST, "description": "Registered table.column names."},
                "flag": {"type": "string", "description": "CLI flag carrying table.column=value."},
                "key": {"type": "string", "description": "Tool argument key carrying the override."},
            },
        },
    },
]
