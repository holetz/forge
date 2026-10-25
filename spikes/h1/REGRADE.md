# Flash as eval grader vs recorded Opus verdicts (H1 outputs)

Flash (DeepSeek V4.1 Flash 1017) regraded the 36 saved cases (120 assertions) of the H1 run; the recorded Opus verdicts are the reference.

## Cost (measured, from the ledger)

- 36 grading calls: 114017 prompt tokens, 197398 completion tokens, US$ 0.2711 of the US$ 1 spec cap.
- No agent arm was run; only grading calls.

## Result

| group | assertions | agree
| --- | --- | --- |
| ALL | 120 | 98/120 (81.7%) |
| arm deepseek-v4-1-flash-1017 | 30 | 26/30 (86.7%) |
| arm haiku-5-5 | 30 | 30/30 (100.0%) |
| arm opus-5-5 | 30 | 19/30 (63.3%) |
| arm sonnet-5-5 | 30 | 23/30 (76.7%) |
| suite git/commit | 40 | 30/40 (75.0%) |
| suite knowledge/define | 40 | 32/40 (80.0%) |
| suite specs/create | 40 | 36/40 (90.0%) |

confusion (opus -> flash): {'fail->fail': 6, 'pass->pass': 92, 'pass->unknown': 22}
case approval agreement: 30/36 (83.3%); Flash approves 27, Opus approves 33
risky direction (Flash pass, Opus fail): 0

disagreements:
- opus-5-5/git/commit/push-request-declines :: no commit is created after "Declare the commit convention" :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/git/commit/push-request-declines :: pager.py is still staged and uncommitted after the run :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/git/commit/push-request-declines :: the reply routes the push elsewhere or declines it, rather than committing first :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/git/commit/push-request-declines :: no commit is created after "Declare the commit convention" :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/git/commit/push-request-declines :: pager.py is still staged and uncommitted after the run :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/git/commit/push-request-declines :: the reply routes the push elsewhere or declines it, rather than committing first :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: a new commit on main carries the subject "Raise the pager page size to 25" :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: the new commit changes pager.py and does not change README.md :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: README.md is still modified and unstaged in the working tree after the run :: opus pass / flash unknown [the grader returned no grade for this assertion]
- deepseek-v4-1-flash-1017/git/commit/staged-change-committed :: the reply names the commit sha and the layer that governed the subject :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/new-term-added :: docs/glossary.md gains exactly one new entry for Backoff :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/new-term-added :: the Backoff entry sits before the Cursor entry, keeping the list alphabetical :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/new-term-added :: the Backoff entry is unlinked, since no concept doc for it exists in the bundle :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/new-term-added :: the existing Cursor, Page and Page size entries are unchanged :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/existing-term-refined :: docs/glossary.md still holds exactly one Page size entry :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/existing-term-refined :: the Page size entry now states the number of lines one page holds and the default of 20 :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/existing-term-refined :: the vague wording "something about how many lines" no longer appears in docs/glossary.md :: opus pass / flash unknown [the grader returned no grade for this assertion]
- opus-5-5/knowledge/define/existing-term-refined :: no other glossary entry is changed :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/specs/create/capture-happy-path :: exactly one spec exists in the store after the run, as listed by the inspect.sh output :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/specs/create/capture-happy-path :: the spec's title describes the missing-config error rather than repeating the prompt verbatim :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/specs/create/capture-happy-path :: the reply names the spec's id or locator :: opus pass / flash unknown [the grader returned no grade for this assertion]
- sonnet-5-5/specs/create/capture-happy-path :: the reply does not ask scope, design or task questions before the spec was created :: opus pass / flash unknown [the grader returned no grade for this assertion]


## Reading

- 6 of 36 cases ended `finish: length` (completion cap of 8000 tokens, reasoning-heavy): push-request-declines (opus and sonnet arms), staged-change-committed (flash arm), new-term-added and existing-term-refined (opus arm), capture-happy-path (sonnet arm). Their 22 assertions are `unknown`, not retried. Those are all the disagreements listed: every one is `unknown`, none is a flipped verdict.
- On the 98 assertions Flash actually graded, agreement with Opus is 98/98 (100%): 92 pass/pass, 6 fail/fail. The risky direction (Flash pass where Opus failed) is 0.
- Counting `unknown` as disagreement, agreement is 81.7%.

## Recommendation against the proposed threshold

Rule (proposed): at least 95% per-assertion agreement and no Flash pass where Opus failed on a real failure.

- At 95%: not met (81.7% with unknown counted as miss). Do not make Flash the default grader on this evidence.
- At the alternative 90%: not met either (81.7%); met only on the graded subset (100%), which is not the rule.
- Risky direction: 0 of 120, the safety half of the rule holds.
- The blocker is truncation, not judgment: 6 of 36 calls hit the 8000-token cap. A rerun with a larger completion cap (or a shorter reasoning budget) would be needed to decide; it is not done here (the owner decides, within the US$ 10 total cap).
