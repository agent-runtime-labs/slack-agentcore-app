# Infra and Dev Loop SOTA Report — 2026 (run 2)

Dream Cycle 2026-09-29, second run · slot 4 · deep `infra-and-dev-loop` · scans `terraform`, `tilt` · parent `fc2c5e80ad4de42847c63b8322b7e8de61d6a1dc`

## TL;DR

The worker's timeout chain is spread over three files: the agent call's read timeout (`agent_client.TIMEOUT_SECONDS = 120`), the worker Lambda timeout (`agent_worker_fn`, 150 s) and the processing queue's visibility timeout (900 s). The only thing tying them together is a comment ("Must exceed the worker Lambda timeout"), and 900 sits exactly on AWS's 6 × function-timeout floor. On the parent, `make test` stays green under 3 of 3 seeded unsafe changes to that chain. The candidate adds a static test that reads the committed Terraform and Python, and it turns all 3 red while leaving a benign control green. Verdict: **ACCEPT** (a recommendation for human review, not a promotion).

## Control plane (STEP 0.5)

| Capability | Available | Notes |
|---|---|---|
| uv | yes | runs `make test` |
| make dream-check | yes | 5 slots, 1 ledger row before tonight |
| make test | yes | parent: 108 + 129 + 22 = **259 passed** |
| terraform | **no** | `tf-fmt` evaluator blocked; tonight's diff changes no `.tf` file |
| tilt | no | Tiltfile change is a deps entry only |
| LLM API key | no | `LLM_EVAL=blocked`; candidate needs no model calls |
| GitHub | MCP tools (issues, PRs); no gist tool | `GIST=LOCAL`: this file is the durable artifact |

## Ledger check

Earlier run tonight: issue #31 CLOSED (completed), PR #32 MERGED by a human. No finding repeated ≥3 nights; merge rate 1/1, so no bias to smaller candidates. `LLM_EVAL=blocked` streak: 2 runs, so a no-model-call candidate was picked again. Tonight picks up next step #2 from run 1.

## Candidate findings (STEP 3)

Scored 1–5 on fit / novelty / testability / measurability / production value / reviewability.

| # | Finding | Evidence | Score | Picked |
|---|---|---|---|---|
| 1 | Timeout chain agent read (120) < worker Lambda (150) ≤ visibility/6 (900) is unguarded; a single edit breaks it with `make test` green | A: seeded mutations, measured locally. The 6× rule itself: B (AWS Lambda docs guidance, not re-fetched tonight) | 5/4/5/5/4/5 = 28 | **yes** |
| 2 | terraform missing from the nightly container, so `tf-fmt` never runs | A: `terraform: command not found` (2nd run) | 4/2/2/2/3/4 = 17 | no; needs an environment setup-script change, which is outside the repo |
| 3 | Tilt `unit-tests` does not watch `infra-as-code/`, so a tf edit never reruns tests locally | A: Tiltfile deps list | 3/3/4/3/2/5 = 20 | folded into #1 (the new test reads `tf-app`) |
| 4 | No `.tiltignore`; `__pycache__` writes may retrigger reruns | C: inference, unmeasured | 3/2/2/2/2/4 = 15 | no |
| 5 | DLQ `maxReceiveCount = 2` means one Lambda timeout plus one retry sends a message to the DLQ with no alarm defined | B: read from data-stores.tf; alarm absence observed | 3/3/2/2/3/3 = 16 | no; needs a design decision about alerting |

External research was kept to zero: the finding is internal, and its evidence is measured locally.

## Competitors

Context rows only, not re-researched tonight (grade C).

| Product | Relevance |
|---|---|
| Slack AI | Hosted by Slack; no customer-side queue/Lambda chain |
| Amazon Q Developer in Slack | AWS-managed; timeouts not customer-configured |
| Glean | SaaS; not comparable at this layer |
| Dust | SaaS agent platform; not comparable at this layer |

## Frozen hypothesis (written before the candidate)

> Given 4 seeded single-value mutations to the committed timeout chain (M1 worker timeout 150→200; M2 visibility 900→300; M3 `TIMEOUT_SECONDS` 120→180; C1 visibility 900→1200, a benign control), when a static timeout-budget test is added to `scripts/tests`, the number of unsafe mutations that turn `make test` red should rise from 0/3 (parent) to 3/3. Invariants: 0/1 false positives on C1; unmutated `make test` green; no test weakened, skipped or deleted; no `.tf` value changed; no AWS or Slack call.

## Evaluation receipt

Each row runs the full `make test` in a throwaway copy of the repo with one mutation applied. The harness confirms each sed changed exactly one line.

| Mutation | Parent rc / scripts suite | Candidate rc / scripts suite |
|---|---|---|
| none | 0 / 22 passed | 0 / 25 passed |
| M1 worker 150→200 | 0 / 22 passed (missed) | **2 / 1 failed** |
| M2 visibility 900→300 | 0 / 22 passed (missed) | **2 / 1 failed** |
| M3 agent read 120→180 | 0 / 22 passed (missed) | **2 / 1 failed** |
| C1 visibility 900→1200 | 0 / 22 passed | 0 / 25 passed (correct) |

Lambdas (108) and agent (129) suites passed in every row. Effect: unsafe mutations caught 0/3 → 3/3; false positives 0/1 → 0/1; tests 259 → 262 passing. The measurement is deterministic, so no statistical test is needed.

## Darwin

Not run. There is no mutation space worth exploring: the test encodes two inequalities. The critic probed the boundaries (worker 151: red; agent read = worker: red).

## Evidence

- MEASUREMENT: parent catches 0/3 seeded unsafe mutations; candidate catches 3/3; control C1 green on both.
- MEASUREMENT: candidate `make test` is 108 + 129 + 25 passed.
- OBSERVATION: live values sit exactly on the 6× line (900 = 6 × 150); headroom between agent read and worker timeout is 30 s.
- OBSERVATION: `terraform` and `tilt` are absent from the nightly container.
- INFERENCE: raising the worker timeout (for slower tools) without the queue would cause duplicate Slack answers in production, with no signal before deploy.
- DECISION: recommend the candidate for human review (draft PR).

## Reward-hack check (independent critic)

An independent subagent with no edit rights reproduced the receipt and returned `CRITIC: CLEAR`. It found no weakened test, threshold, gold data or product value, and confirmed the test checks relations, not the literals 150/900/120. Non-blocking notes, both addressed:

- The docstring overstated "gives up before the Lambda is killed": the read timeout is per socket read, so the check is a floor. The wording and test name now say so.
- An annotated `TIMEOUT_SECONDS: int = 120` would fail with a misleading message. The parser now accepts it.

Known remaining gaps: moving a value to a variable fails loudly ("no literal ..."), not silently. The HCL parsing assumes `terraform fmt` layout.

## Security review

No IAM, network, token or Slack surface changes. The test only reads committed files. Per-user token isolation is untouched. Duplicate answers (the failure mode guarded) are a correctness issue, not a security one.

## Next steps

1. Add terraform to the nightly environment's setup script so `tf-fmt` can run on slot-4 nights (2nd night blocked).
2. Decide on a CloudWatch alarm for the processing DLQ (finding #5); then a static check that it exists.
3. Measure the real worst-case worker duration (triage + agent + Slack posts) from logs before trusting the 30 s headroom.

## Witness

- Session commit: `fc2c5e80ad4de42847c63b8322b7e8de61d6a1dc`
- Report sha256 (this file with the Witness section body replaced by the single line `PENDING`): `4358de7642ab13dae34136bd1fd59024f2ede6a3110e58fbb3874175421439a5`
- Witness stamp: `b2070b597931eee79f34372e2830db2c98df6c865df0fa50c5f5adff6c2e2ed8`

Verify:
1. `git show fc2c5e8` exists in the repo history.
2. Copy this file and replace everything after `## Witness` with a blank line, `PENDING`, and a trailing newline.
3. `sha256sum` the copy and compare it with the report sha256 above.
4. `printf '%s%s' <report sha256> fc2c5e80ad4de42847c63b8322b7e8de61d6a1dc | sha256sum` gives the witness stamp.
5. Re-run the receipt: in a copy of the dream branch, apply each mutation from the table with `sed` and run `make test`; M1–M3 must fail `scripts/tests/test_timeout_budget.py`, and C1 and the unmutated tree must pass. Delete the new test file and all five must pass.
