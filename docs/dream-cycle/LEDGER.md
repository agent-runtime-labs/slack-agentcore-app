| Date | Deep | Finding | Issue | PR | Evaluated? | Verdict | Effect | Witness | Prior-night fates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-29 | infra-and-dev-loop | Tilt unit-tests skipped scripts/tests (drift from make test); LLM_EVAL=blocked, tf-fmt blocked (no terraform) | #31 | #32 (draft) | yes | ACCEPT | drifted suites 1->0; tests 256->259 | 15b6353b | first night (no prior rows) |
| 2026-09-29 | infra-and-dev-loop | Worker/queue timeout chain (agent read 120 < worker 150 <= visibility 900/6) unguarded; run 2; LLM_EVAL=blocked, tf-fmt blocked (no terraform) | #34 | #35 (draft) | yes | ACCEPT | unsafe timeout edits caught 0/3->3/3, FP 0/1; tests 259->262 | b2070b59 | #31 CLOSED, #32 MERGED |
