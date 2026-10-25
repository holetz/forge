# H1 report — does the cheap model run quenching with acceptable quality

Date 2026-10-24. Source of every number: `python3 spikes/h1/report.py spikes/h1/results` (re-runs in `results/rerun/` merged over the lost cases). No model run was made to write this report.

## Verdict

- **Flash 1017 passes H1**: 9/9 cases, 30/30 assertions, US$ 0.19 of agent cost; it is close to the baseline on all three suites.
- **Haiku 5.5 fails H1**: it misses `knowledge/define` (2/3 cases) and `specs/create` (2/3 cases, 80% pass rate, 20 points under the baseline).
- **Light tier: Flash 1017 over Haiku 5.5.** Haiku is 5x cheaper on the agent run (US$ 0.04 vs 0.19) but loses quality; Flash keeps the baseline's quality.
- **Hybrid routing: no.** The default candidate (Flash 1017) passes every suite, so routing the hard cases to a bigger model is not needed on this evidence.
- The 80% / 10-point threshold below is a **proposal for the owner**, not a settled rule.

The sample is small: 3 suites x 3 cases = 9 cases per arm, 30 assertions. One case is 11 points of approval and 3.3 points of pass rate. Read the verdict as "no evidence against Flash", not as a measured equivalence.

## Endpoint facts

- The subject is `system.ai.deepseek-v4-1-flash-1017`, reachable only through Unity Gateway: `POST <host>/ai-gateway/mlflow/v1/chat/completions` (served model `deepseek-ai/DeepSeek-V4.1-Flash-1017`, reasoning in `reasoning_content`). The legacy `/serving-endpoints/databricks-deepseek-v4-1-flash-1017` refuses ("available only via Unity Gateway").
- `databricks-deepseek-v4-1-flash` and `databricks-deepseek-v4-flash-0731` are listed `READY` but every call answers `BAD_REQUEST ... is deprecated`. Epic 1 §Model routing's earlier default model was unreachable.
- GLM 5.3 Flash was the interim subject by a delegated decision, reversed by the owner the same day; that arm was stopped after one case, never graded, and is only a fallback note.
- Price (US$ per 1M, in / cache read / out): Flash 0.30 / 0.03 / 1.20, from the epic's table in `spikes/h1/prices.json`. Haiku 5.5 uses the short-context tier.
- One harness for every arm (OpenCode 1.19.4 with the quenching-opencode port, same cases, same turn cap), model the only variable; grader fixed at Opus 5.5. The command is invoked explicitly, so trigger accuracy is not measured; the `Not for:` boundary cases are.

## Approval per suite

Case approval = cases whose assertions all passed. Pass rate = assertions passed / 10 per suite.

| Arm | git/commit | knowledge/define | specs/create | ALL cases | ALL pass rate |
| --- | --- | --- | --- | --- | --- |
| opus-5-5 (baseline) | 3/3, 100% | 3/3, 100% | 3/3, 100% | 9/9 (100%) | 100.0% |
| deepseek-v4-1-flash-1017 | 3/3, 100% | 3/3, 100% | 3/3, 100% | 9/9 (100%) | 100.0% |
| sonnet-5-5 | 3/3, 100% | 3/3, 100% | 2/3, 80% | 8/9 (88.9%) | 93.3% |
| haiku-5-5 | 3/3, 100% | 2/3, 80% | 2/3, 80% | 7/9 (77.8%) | 86.7% |

No case was `unknown` and none ended in an arm error after the re-runs: task 2.7 re-ran, one at a time, the five cases the parallel run lost to infrastructure (opus 2, haiku 1, sonnet 2), and `report.py` prefers `results/rerun/`.

## The threshold (proposal)

An arm is *close to the baseline* on a suite when its case approval rate is at least 80% of the baseline's and its assertion pass rate is within 10 points; an arm passes H1 when it is close on all three suites. Applied to the measured spread:

| Arm | git/commit | knowledge/define | specs/create | H1 |
| --- | --- | --- | --- | --- |
| Flash 1017 | close | close | close | pass |
| Sonnet 5.5 | close | close | no (pass rate 80%, 20 pts) | fail |
| Haiku 5.5 | close | no (pass rate 80%) | no (pass rate 80%) | fail |

With 3 cases per suite a single failed case is 33% of approval, so the rule is effectively "no failed case on any suite" for 3-case suites. The owner confirms or replaces the rule when reading this file.

## Cost and volume

Agent run, from the events' token classes and the price table:

| Arm | Total tokens | Cache read | US$ agent | Grader US$ (estimate, upper bound) |
| --- | --- | --- | --- | --- |
| opus-5-5 | 512,034 | 319,132 | 1.1727 | 0.7933 |
| sonnet-5-5 | 679,937 | 439,115 | 0.7816 | 0.8049 |
| deepseek-v4-1-flash-1017 | 2,304,384 | 2,050,304 | 0.1856 | 0.9378 |
| haiku-5-5 | 679,169 | 485,051 | 0.0359 | 0.8075 |

- Flash used **4.5x Opus's tokens** (2.30M vs 0.51M; 89% cache read), so its price advantage (6.3x cheaper than Opus on the agent run) comes from the unit price, not from fewer tokens. Long `specs/create` runs dominate (1.73M tokens).
- The grader columns are an estimate (Opus 5.5, about 54-79k input per suite); they are not measured per call.

## Databricks cross-check (read once by the conductor, 2026-10-24, partial)

Read from system tables while ingestion still lagged: `system.billing.usage` was still empty, and `ai_gateway.usage` stops at 23:32 UTC, so the Flash 1017 arm is not in it yet. `system.serving.endpoint_usage`, up to about 23:41 UTC; tokens are input / output, and input includes cache reads:

| Endpoint | Requests | Input | Output |
| --- | --- | --- | --- |
| opus-5-5 (agent + grader) | 55 | 827,737 | 19,789 |
| haiku-5-5 | 52 | 808,471 | 18,691 |
| sonnet-5-5 | 34 | 623,604 | 11,799 |
| glm-5-3-flash (stopped arm, never graded) | 56 | 2,624,099 | 56,523 |

- The order of magnitude agrees with the report; the figures do not reconcile exactly, and no cause is asserted. The report counts only the runs kept in `results/` (plus the grader estimate); the endpoint counts every request, including the lost and re-run attempts.
- The GLM arm cost about **US$ 0.42** as an upper bound (2.62M input, 0.06M output) for one ungraded case, a reminder of what a stopped arm still pays.
- The cross-check is partial and one-time: billing in US$ is still to be confirmed once `system.billing.usage` fills, and Flash 1017 once the gateway usage catches up.

## Caveats

- 9 cases per arm; one grader (Opus 5.5) shared by the baseline, which may favour the baseline's style.
- Flash's token volume is high; with a real price change or a lower cache hit rate the saving would shrink.
- Haiku's re-run (knowledge/define new-term-added) was graded once; Sonnet's and Opus's re-runs likewise.
- The threshold is an assumption of this spike; hybrid routing is "no" only under it.
