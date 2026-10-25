# Policy pack

forge ships a generic set of Omnigent server policies in `policy/`. They run in the server
process, so an agent cannot rewrite them by writing code. They are a visible, auditable first
layer: the hard layers are the sandbox, the egress proxy and the model gateway.

## How it loads

`compose.yaml` mounts `./policy` read-only at `/policy` and sets `OMNIGENT_CONFIG=/policy/config.yaml`
and `PYTHONPATH=/policy` on the `omnigent` service (pinned v0.19.1). The config lists
`policy_modules: [forge_policies]` and a `policies:` mapping; each entry is a `type: function` with a
`handler` and `factory_params`. The config is rendered, never hand-edited:

```bash
python3 -I scripts/render-policy-config.py                 # defaults -> policy/config.yaml
python3 -I scripts/render-policy-config.py --check         # exit 1 when the file drifted
bash scripts/smoke-local.sh                                # the pinned server's own loader resolves them
```

## Policies

| Policy | What it does | Verdict |
|---|---|---|
| `forge__approval` | a shell command whose word sequence is in `approval.commands`, or a tool matching `approval.tools`, parks for approval | `approval.action` (`ASK` default) |
| `forge__egress` | extracts hosts from URLs, `user@host:` forms and network commands; `deny_hosts` always wins, a non-empty `allow_hosts` denies the rest | `DENY`; `ASK` when no host can be parsed (`action_unparsed`) |
| `forge__output` | scans `response` and `tool_result` text for secrets and personal data; the reason carries a category and a count, never the match | `output.action` (`DENY` default) |
| `forge__session_budget`, `forge__daily_budget` | Omnigent's `cost_budget` and `user_daily_cost_budget` | soft `ASK` thresholds, hard gate on expensive models |
| `forge__tool_call_limit` | Omnigent's `max_tool_calls_per_session` | the builtin's verdict |

## Parameters

`policy/params.default.json` holds the defaults; an overlay's `policy/params.json` overrides them
group by group, key by key (a list replaces the default list whole). Unknown keys, wrong types and
budgets whose soft thresholds are not below the hard limit are refused.

| Group | Keys |
|---|---|
| `approval` | `commands`, `tools`, `action` |
| `budget` | `session_max_usd`, `session_ask_usd`, `daily_max_usd`, `daily_ask_usd`, `expensive_models`, `max_tool_calls`; a `null` limit drops that policy |
| `egress` | `allow_hosts` (loopback by default), `deny_hosts`, `network_tools`, `shell_network_commands`, `action_unparsed` |
| `output` | `detect` (`private_key`, `dapi_token`, `cloud_key`, `vcs_token`, `jwt`, `cpf`, `cnpj`, `credit_card`, `ssn`; opt-in `email`, `phone`), `extra_patterns`, `action`, `phases` |

## Limits

- Pattern checks are bypassable: an encoded command, a script file, split tokens or a secret in an unusual shape pass. `echo git push` asks (a false positive accepted over failing open).
- The budget policies are a downgrade gate, not a hard stop; the hard block belongs to the model gateway.
- Output is blocked or asked, never redacted in place.
- The egress policy is a second layer in front of the egress proxy and the sandbox, not a replacement.

## Overlay use

An overlay keeps its values in `policy/params.json` (checked by `scripts/overlay-check.py`), renders
its own config with `--params policy/params.json --out <overlay>/policy/config.yaml`, and mounts it
over `/policy/config.yaml` from its `compose.overlay.yaml`. The default `allow_hosts` is loopback
only, so an overlay lists its own git host there. See [overlay-contract.md](overlay-contract.md).

## Upgrade rule

A bump of the Omnigent pin must re-run `bash scripts/smoke-local.sh`: the proof uses the pinned
server's own policy loader, and Omnigent is alpha. The module imports nothing from Omnigent.
