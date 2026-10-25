# Flash as eval grader vs recorded Opus verdicts (H1 outputs)

Flash (DeepSeek V4.1 Flash 1017) regraded the 36 saved cases (120 assertions) of the H1 run; the recorded Opus verdicts are the reference. Spec 14 graded them at an 8,000-token completion cap and six ended truncated; spec 16 re-graded only those six at 16,000 (the table below is the merged set; the first-pass figures are kept in "First pass").

## Cost (measured, from the ledgers)

- First pass (spec 14): 36 grading calls, 114017 prompt tokens, 197398 completion tokens, US$ 0.2711 of its US$ 1 cap.
- Retry (spec 16): 6 grading calls, one at a time, 11722 prompt tokens (9216 cached), 55067 completion tokens, US$ 0.0671 of its US$ 0.25 cap (`RETRY_CAP_USD`, checked against the worst-case price before every call).
- Total for the grading: US$ 0.3382. No agent arm was run; only grading calls.

## Result (merged: 30 first-pass grades + 6 retried at 16,000)

| group | assertions | agree
| --- | --- | --- |
| ALL | 120 | 116/120 (96.7%) |
| arm deepseek-v4-1-flash-1017 | 30 | 26/30 (86.7%) |
| arm haiku-5-5 | 30 | 30/30 (100.0%) |
| arm opus-5-5 | 30 | 30/30 (100.0%) |
| arm sonnet-5-5 | 30 | 30/30 (100.0%) |
| suite git/commit | 40 | 36/40 (90.0%) |
| suite knowledge/define | 40 | 40/40 (100.0%) |
| suite specs/create | 40 | 40/40 (100.0%) |

confusion (opus -> flash): {'fail->fail': 6, 'pass->pass': 110, 'pass->unknown': 4}
case approval agreement: 35/36 (97.2%); Flash approves 32, Opus approves 33
risky direction (Flash pass, Opus fail): 0


disagreements:
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: a new commit on main carries the subject "Raise the pager page size to 25" :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: the new commit changes pager.py and does not change README.md :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: README.md is still modified and unstaged in the working tree after the run :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: the reply names the commit sha and the layer that governed the subject :: opus pass / flash unknown [the grader returned no grade for this assertion]

## Retried cases

| case | completion tokens | finish | graded |
| --- | --- | --- | --- |
| opus-5-5 / git/commit / push-request-declines | 10743 | stop | 3/3 agree |
| sonnet-5-5 / git/commit / push-request-declines | 5947 | stop | 3/3 agree |
| deepseek-v4-1-flash-1017 / git/commit / staged-change-committed | 16000 | length | still truncated: 4 assertions stay `unknown`, not retried |
| opus-5-5 / knowledge/define / new-term-added | 5718 | stop | 4/4 agree |
| opus-5-5 / knowledge/define / existing-term-refined | 8261 | stop | 4/4 agree |
| sonnet-5-5 / specs/create / capture-happy-path | 8398 | stop | 4/4 agree |

Five of six finish inside 16,000 tokens (three of them, at 10743, 8261 and 8398 completion tokens, would not have fit the old 8,000 cap). One still ends `finish: length` at 16,000 and stays `unknown`.

## First pass (8,000-token cap, spec 14)

Overall 98/120 (81.7%); per arm: flash 26/30, haiku 30/30, opus 19/30, sonnet 23/30; per suite: git/commit 30/40, knowledge/define 32/40, specs/create 36/40. All 22 disagreements were `unknown` from truncation.

## Reading

- On the 116 assertions Flash graded, agreement with Opus is 116/116 (100%): 110 pass/pass, 6 fail/fail. The risky direction (Flash pass where Opus failed) is 0 of 120.
- Counting `unknown` as disagreement, agreement is 116/120 (96.7%), up from 81.7%. The 4 misses are one case, all `unknown`, none a flipped verdict.
- Case approval agreement is 35/36; Flash approves 32 cases, Opus 33 (the gap is the still-truncated case).
- The evidence base for the fail direction is thin: only 6 assertions in the 120 were failures.

## Recommendation against the proposed threshold

Rule (proposed): at least 95% per-assertion agreement and no Flash pass where Opus failed on a real failure.

- At 95%: met (96.7%, `unknown` counted as a miss). At the alternative 90%: met.
- Risky direction: 0 of 120; the safety half holds.
- Recommendation: make Flash the default grader, with two conditions. (1) Run it with a completion cap of at least 16,000 tokens (a cap of 8,000 truncated 6 of 36 cases; 1 of 36 still truncates at 16,000). (2) Treat `unknown` as ungraded, never as a pass: escalate it to Opus or rerun with a larger cap, which the runner's evidence rule already supports. Re-measure when more failing assertions exist, since 6 fail/fail cases make the safety evidence narrow.
- The decision to change the eval runner's default stays with the owner; this spike changes no runner.
