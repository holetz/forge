# Spike H6: porting agent hooks to platform policies

Verdict: **H6 holds for the decision logic, and holds only partly for coverage.** Two
`PreToolUse` guard patterns port to Omnigent function policies with the S16 mechanism and the
pinned server's own loader resolves them. The policy matched the hook on 47 of 51 cases and was
never wrong where the hook was right on an allow case. Four cases diverge, all listed below, and
the hooks stay on in the harness.

All values are synthetic (`synthcli`, `team-a-*`, `sales.*`); no adopter hook, host or registry
was used, and no model or Databricks call was made.

## Method

- Baseline: two stdin-JSON hooks in `spikes/h6/hooks/` (compute guard, column-override guard),
  written the usual way with regular expressions over the raw command text; they block with exit 2.
- Port: `spikes/h6/policy/h6_policies/` exposes `compute_guard` and `column_override_guard`
  factories plus `POLICY_REGISTRY`, named in `spikes/h6/policy/config.yaml` (`policy_modules`,
  `policies:`, `type: function`, `factory_params`). Stdlib only, shlex per command segment.
- Corpus: `spikes/h6/corpus.json`, one neutral case per call (tool, arguments, expected verdict).
  `spikes/h6/tests/test_parity.py` builds the hook event and the policy event from it, runs the
  hook as a subprocess, compares the verdicts, and fails on an unlisted divergence, on a listed
  one that stopped diverging, on a policy false positive, and on a divergence with no reason. It
  runs in `scripts/verify_repo.sh`.
- Loader proof: `spikes/h6/prove-loader.sh` runs one `docker run --rm` on the image pinned in
  `compose.yaml` (v0.19.1), mounts the spike policy read-only, and drives the server's own
  `load_registry` / `parse_default_policies` / `resolve_function_policy`. Six synthetic events
  returned the expected verdicts.

## Corpus

51 cases: 26 compute, 19 column, 6 shared unrelated; 30 expected DENY, 21 expected ALLOW; 4
known divergences.

## Divergences

| Case | Hook | Policy | Reason |
| --- | --- | --- | --- |
| `c-deny-bash-c` | DENY | ALLOW | The hook matches the raw text; the policy tokenizes, and a `bash -c '...'` payload stays one quoted token. |
| `c-deny-subshell` | DENY | ALLOW | Same, for `$(...)` inside another command. |
| `k-deny-bash-c` | DENY | ALLOW | Same, for the column guard. |
| `k-deny-no-value` | ALLOW | DENY | The hook regex needs a value after the flag; the policy fails closed on a bare flag (stricter, not a false positive). |

Three of four are a policy miss on wrapped commands; fixing them needs a recursive unwrap of
`bash -c` and command substitution in the policy. One is the policy being stricter. The two
cases built to provoke a hook false positive (a file write and an `echo` that merely mention a
guarded command) did not diverge: this hook's pattern already ignores them.

## What stays in the harness

- Hooks on events a policy has no phase for: session start, notification, prompt submit.
- Hooks that read the working tree or the repository state; the server has no harness working
  directory, so the registry travels as `factory_params` rendered into the config. Whether a
  policy may read a mounted registry file at evaluation time is still an open decision.
- The Claude Code hooks themselves: they stay on. The policy is the platform-side layer for
  every client, not a replacement.

## Disk and images

Free disk before: 26G; after: 25G. One image pulled by the proof
(`ghcr.io/omnigent-ai/omnigent-server:v0.19.1`, pinned digest from `compose.yaml`) and removed by
the script because the run pulled it. No compose stack, volume or port; no `eclume-*` or
`lumea-*` container touched. Nothing was pulled or removed beyond that.
