| Date | Deep | Finding | Issue | PR | Evaluated? | Verdict | Effect | Witness | Prior-night fates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-29 | infra-and-dev-loop | Tilt unit-tests skipped scripts/tests (drift from make test); LLM_EVAL=blocked, tf-fmt blocked (no terraform) | #31 | #32 (draft) | yes | ACCEPT | drifted suites 1->0; tests 256->259 | 15b6353b | first night (no prior rows) |
| 2026-10-01 | agent-tools | GitHub tool never re-asked consent on MCP 401 (mcp 2.x error text drops HTTP status); LLM_EVAL=blocked, tf-fmt blocked (no terraform) | #38 | #39 (draft) | yes | ACCEPT | 401 re-consent 0/1->1/1; tests 389->391 | a37263bd | 2026-09-29: #31 CLOSED, #32 MERGED |
