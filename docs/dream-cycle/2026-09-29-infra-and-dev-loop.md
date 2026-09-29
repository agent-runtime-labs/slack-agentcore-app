# Infra and Dev Loop SOTA Report — 2026

Dream Cycle 2026-09-29 · slot 4 · deep `infra-and-dev-loop` · scans `terraform`, `tilt` · parent `f5ff56b66fd1ca5701b10200985a3c81c0b8af8c`

## TL;DR

Tilt's **unit-tests** resource ran 2 of the 3 pytest suites that `make test` runs. It skipped `scripts/tests` and did not watch `scripts/`. So a local edit could show green in Tilt and still fail `make test`, which is the nightly scoreboard. The candidate adds the missing suite and paths to the Tiltfile, plus a parity test that fails whenever the two drift again. The parity test fails on the parent and passes on the candidate. Verdict: **ACCEPT** (a recommendation for human review, not a promotion).

## Control plane (STEP 0.5)

| Capability | Available | Notes |
|---|---|---|
| uv | yes (`/root/.local/bin/uv`) | runs `make test` |
| make dream-check | yes | 5 slots, 0 ledger rows before tonight |
| make test | yes | parent: 108 + 129 + 19 = **256 passed** |
| terraform | **no** (not installed) | `tf-fmt` evaluator blocked; tonight's diff touches no `.tf` |
| tilt | no | Tilt's command string was run with `sh -c` instead (see Evaluation) |
| LLM API key | no | `LLM_EVAL=blocked`; picked a candidate that needs no model calls |
| gh / gist tool | no gist capability | `GIST=LOCAL`: this file is the durable artifact |

## Candidate findings (STEP 3)

Scored 1–5 on fit / novelty / testability / measurability / production value / reviewability.

| # | Finding | Evidence | Score | Picked |
|---|---|---|---|---|
| 1 | Tilt `unit-tests` skips `scripts/tests` and does not watch `scripts/` (drift from `make test`) | A: reproduced by parsing both files | 5/4/5/5/3/5 = 27 | **yes** |
| 2 | `docs/testing-guide.md` says `103 passed` (lambdas), but the actual count is 108; `scripts/tests` is not mentioned | A: `make test` output | 4/2/3/3/2/5 = 19 | folded into #1 (same sentence) |
| 3 | `terraform fmt -check` evaluator cannot run in the nightly container (no terraform binary) | A: `terraform: command not found` | 4/3/2/2/3/4 = 18 | recorded; next step |
| 4 | No `.tiltignore`: `__pycache__` writes under watched dirs may retrigger unit-tests | C: critic inference, not measured | 3/2/2/2/2/4 = 15 | no |
| 5 | SQS visibility timeout 900 s = 6 × worker Lambda timeout 150 s, exactly at the AWS guidance floor; raising the worker timeout without the queue breaks it | B: AWS Lambda/SQS docs guidance (not re-fetched tonight) | 3/2/3/2/3/4 = 17 | no. A future candidate is a static check tying the two values together. |

External research was deliberately kept to zero tonight. The finding is internal to the repo, and its evidence is grade A: it was reproduced locally. No external claim supports the decision.

## Competitors

The dev-loop finding is internal, so these are context rows only. They were not re-researched tonight (grade C, taken from general product knowledge and not verified).

| Product | Relevance to tonight |
|---|---|
| Slack AI | Hosted by Slack; no customer-side dev loop to compare |
| Amazon Q Developer in Slack | AWS-managed; no local Tilt-style loop exposed |
| Glean | SaaS; not comparable at the dev-loop layer |
| Dust | SaaS agent platform; not comparable at the dev-loop layer |

## Frozen hypothesis (written before the candidate was evaluated)

> Given the committed `Makefile` `test:` target and the `Tiltfile` `unit-tests` local_resource, when the Tiltfile runs and watches every suite that `make test` runs, the number of `make test` suites that Tilt does not run or watch should fall from 1 on the parent to 0. Invariants: no test weakened, skipped or deleted; `make test` stays green; no `.tf`, AWS or Slack surface is touched.

## Evaluation receipt

The metric is the number of drifted suites, measured by the new `scripts/tests/test_dev_loop_parity.py`. The corpus is the committed Makefile and Tiltfile.

| Run | Parity test | make test |
|---|---|---|
| Parent Tiltfile + new test | **2 failed, 1 passed**. Missing: `{'scripts/tests': '--with pytest'}`; not watched: `['scripts/tests']` | 108 / 129 / 19 (+3 new: 2 fail) |
| Candidate | **3 passed** | 108 / 129 / **22 passed** |
| Candidate Tilt `cmd` run with `sh -c` | n/a | 108 / 129 / 22 passed, exit 0 |

Effect: drifted suites 1 → 0; tests 256 → 259 passing (+3 new, 0 removed). The measurement is deterministic, so no statistical test is needed. `tf-fmt`: blocked (no terraform), and not relevant because no `.tf` file changed.

## Darwin

Not run. There is no mutation space: the fix is a one-line suite addition plus watched paths. Bounded Darwin would only add noise. Failed lineage: none.

## Evidence

- MEASUREMENT: the parent parity test gives 2 failed / 1 passed; the candidate gives 3 / 3.
- MEASUREMENT: candidate `make test` is 108 + 129 + 22 passed.
- OBSERVATION: `terraform` and `tilt` are absent from the nightly container.
- INFERENCE: a developer relying on Tilt could push a change that breaks `scripts/dream_check.py` without a local red signal.
- DECISION: recommend the candidate for human review (draft PR).

## Reward-hack check (independent critic)

An independent subagent reviewed the diff with no edit rights. Its verdict was `CRITIC: CLEAR`. No test or threshold was weakened, and it reproduced the parent failure itself. Known gaps it named:

- A suite removed from *both* files still passes. `>=1` only catches an empty target.
- The `UV` prefix is not compared.
- The doc table lacked rows for `scripts/tests`. That gap is fixed in this candidate.

## Security review

The change is to the local dev loop only. There is no IAM, network, token or Slack surface. The parity test only reads two repo files. Per-user token isolation is untouched.

## Next steps

1. Add terraform to the nightly container's setup script so the `tf-fmt` evaluator can run on slot-4 nights.
2. Add a static check that the SQS `visibility_timeout_seconds` is at least 6 × the `agent_worker_fn` timeout (finding #5).
3. Consider a `.tiltignore` for `__pycache__` / `.pytest_cache` and measure rerun counts before and after (finding #4).

## Witness

- Session commit: `f5ff56b66fd1ca5701b10200985a3c81c0b8af8c`
- Report sha256 (this file with the Witness section body replaced by the single line `PENDING`): `59bee9c0883c990097fd53371fe1d8c64f5da9cec121da8af9d738c3d80482ad`
- Witness stamp: `15b6353bd8d9ec802787b450862fa6dae789061a34fcb8a293fdbbe28b5f4417`

Verify:
1. `git show f5ff56b` exists in the repo history.
2. Copy this file and replace everything after `## Witness` with a blank line, `PENDING`, and a trailing newline.
3. `sha256sum` the copy and compare it with the report sha256 above.
4. `printf '%s%s' <report sha256> f5ff56b66fd1ca5701b10200985a3c81c0b8af8c | sha256sum` gives the witness stamp.
5. Re-run the receipt: `git checkout f5ff56b -- Tiltfile` on the dream branch, then `uv run --quiet --no-project --python 3.13 --with pytest pytest -q scripts/tests/test_dev_loop_parity.py` (expect 2 failed); restore the branch Tiltfile and expect 3 passed.
