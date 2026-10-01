# Agent Tools SOTA Report — 2026

Dream Cycle 2026-10-01 · slot 1 · deep `agent-tools` · scans `github-tool`, `linkedin-tool` · parent `ea7bfa6168a5e4e69c010c41e81f41e211475007`

## TL;DR

A user who revokes the GitHub app (or whose token goes stale) is never asked to reconnect. Instead, every GitHub question returns `ERROR: could not reach GitHub right now`. The cause: `use_github` decides "the token was rejected" by searching the exception text for `"401"`. With the strands-agents (1.57.1) and mcp (2.1.1) versions that `requirements.txt` resolves to today, the MCP client turns every HTTP error status into the same text: `the client initialization failed: unhandled errors in a TaskGroup (1 sub-exception)`. A 401 and a 500 are indistinguishable by text, so the re-consent branch is dead code. `docs/github-setup.md` promises the opposite ("The next call gets a 401, and the tool automatically asks them to connect again").

The candidate sends the token through a small `httpx.Auth` hook that sees the real response status. When that status is a 401, it re-raises an explicit `HTTP 401` error. A loopback stub of the MCP server measures the effect on the real client stack: on a 401, re-consent goes from **0/1 to 1/1**. On a 500 the user still gets an error and no consent prompt. Verdict: **ACCEPT**, meaning recommended for human review; nothing is promoted.

## Control plane (STEP 0.5)

| Capability | Available | Notes |
|---|---|---|
| uv | yes (`/root/.local/bin/uv`) | runs `make test` |
| make dream-check | yes | 5 slots, 1 ledger row before tonight |
| make test | yes | parent: 216 + 151 + 22 = **389 passed** |
| terraform | no | `tf-fmt` blocked; tonight's diff touches no `.tf` |
| LLM API key | no | `LLM_EVAL=blocked`; candidate needs no model calls (the stub fails before any model is built) |
| GitHub MCP tools | yes | issue + draft PR; no gist tool, so `GIST=LOCAL` |
| Installed stack | strands-agents 1.57.1, mcp 2.1.1, httpx 0.28.1 | unpinned (`strands-agents>=1.30.0`, no lock file) |

## Ledger check and learning signals

There is 1 prior row (2026-09-29, infra-and-dev-loop, ACCEPT). Its PR #32 was **MERGED** and its issue #31 is **CLOSED** (completed). No finding has repeated, the merge rate is 1/1, and `LLM_EVAL=blocked` is now a 2-night streak. Following the streak signal, tonight's candidate needs no model calls.

## Candidate findings (STEP 3)

Scored 1–5 on fit / novelty / testability / measurability / production value / reviewability.

| # | Finding | Evidence | Score | Picked |
|---|---|---|---|---|
| 1 | `use_github` never re-asks for consent on a 401, because the MCP client's error text has no status code | A: reproduced on the installed stack with a loopback 401/403/500 server; all three give identical text | 5/5/5/5/5/4 = 29 | **yes** |
| 2 | `cimd/tool.py:121` uses the same `"401" in str(err)` check on the same `MCPClient(url=..., headers=...)` path, so CIMD connections are probably not cleared on a 401 either | INFERENCE (same code path; not measured separately tonight) | 5/4/5/5/4/3 = 26 | no. The hypothesis was frozen to GitHub only. This is next step 1 |
| 3 | `docs/github-setup.md:108` says a revoked token gets an automatic reconnect prompt, which is false on the parent | A: follows from #1 | 4/2/3/2/3/5 = 19 | becomes true with #1 |
| 4 | `docs/testing-guide.md` describes `test_github_tool.py` as testing "against `api.github.com/user`", but the tool moved to the MCP server | A: file read | 3/2/3/1/2/5 = 16 | folded in (one row) |
| 5 | LinkedIn: a 403 (missing scope after a scope change) returns `HTTP 403` with no re-consent; only 401 re-asks | C: inference, LinkedIn's behaviour on scope loss not verified | 3/3/4/3/2/4 = 19 | no. Recorded as a scan finding |

## Scan findings

**github-tool**
- Finding #1 (fixed in the candidate).
- A token revoked *after* a successful MCP initialize (a 401 on a later tool call) still reaches the nested agent as a tool error, so no re-consent happens. This is unchanged from the parent; observed by the critic, not measured.
- `httpx` was imported only transitively. The candidate now declares `httpx>=0.28.1`, because strands core requires `httpx>=0.28.1,<1`.

**linkedin-tool**
- It uses plain `urllib`, so `HTTPError.code` is exact and its 401 path works. The unit test covers this with a faked status, and the code path does not depend on any error text. Grade A (code read).
- Finding #5: a 403 is not treated as "consent needed". This is a low-priority candidate.

## Competitors (context rows only; grade C, not re-researched tonight)

| Product | Re-auth behaviour relevant to tonight |
|---|---|
| Slack AI | First-party data only; no per-user third-party OAuth tool to re-consent |
| Amazon Q Developer in Slack | Uses AWS account / IAM identity, not a per-user third-party token vault |
| Glean | Admin-level connectors; per-user re-auth not exposed in chat |
| Dust | Per-workspace connections with OAuth; reconnect prompts in UI (single-source, unverified) |

External basis for the change: the MCP authorization spec says servers answer invalid or expired tokens with HTTP 401. That makes a 401 on initialize the correct signal to trigger re-consent. Grade B: recalled from the spec, not re-fetched tonight. The decision itself rests on the grade-A local reproduction.

## Frozen hypothesis (written before the candidate was evaluated; sha256 `9346325d…f67d`)

> Given the GitHub MCP endpoint answering HTTP 401 (revoked/expired user token), reproduced by a loopback HTTP stub driven through the real installed strands/mcp stack (no network, no AWS), when use_github detects the rejection from the HTTP status seen by an httpx.Auth hook instead of from the exception text, then the share of 401 cases that trigger forced re-consent (fetch_token(force=True) -> AUTHORIZATION_REQUIRED) should rise from 0/1 on the parent to 1/1. It must hold that: a 500 still returns "ERROR: could not reach GitHub right now" with exactly one fetch_token call (no spurious consent prompt); the stub sees "Bearer <this user's token>"; every existing test passes unmodified; and no test is weakened, skipped or deleted.

## Candidate

- `src/github.py`: adds `_BearerAuth(httpx.Auth)`. It sets `Authorization: Bearer <token>` and records a 401 on the response. `_run_github_mcp_agent` passes it as `auth_provider`, replacing the raw `headers`. If initialize fails after a 401, it re-raises `MCPClientInitializationError("GitHub rejected the access token (HTTP 401)")`. The caller in `use_github` is unchanged.
- `tests/test_github_tool.py`: adds a loopback MCP stub fixture and two tests, one for 401 (re-consent) and one for 500 (no re-consent). The 5 existing tests are byte-identical.
- `requirements.txt`: adds `httpx>=0.28.1`, which is now a direct import.
- `docs/testing-guide.md`: corrects the `test_github_tool.py` row.

## Evaluation receipt

The evaluator is `make test` plus the new stub tests. The corpus is the loopback stub answering 401 or 500.

| Run | `test_github_tool.py` | 401 → re-consent | 500 → error, 1 fetch | make test |
|---|---|---|---|---|
| Parent `github.py` + new tests (×3) | **1 failed, 6 passed** each time | **0/1**: returned `ERROR: could not reach GitHub right now` | yes | 216 / 151+2 new (1 fail) / 22 |
| Candidate (×5, plus 8 by the critic) | **7 passed** each time | **1/1** | yes | 216 / **153** / 22, all passed |

Effect: 401 re-consent goes from 0/1 to 1/1, and tests go from 389 to 391 passing (+2 new, 0 changed or removed). The test is deterministic: every run gave the same result, 13 candidate runs in all. `tf-fmt` is blocked (no terraform) and not relevant, since no `.tf` file changed. `LLM_EVAL=blocked`, but no model call is on this path.

## Darwin

Not run. The fix has no meaningful mutation space (one auth hook and one re-raise), and bounded Darwin would add noise. Failed lineage: none. One alternative was considered and rejected before any code was written: walking `__cause__`/`ExceptionGroup` for the status. The status is not there, because mcp 2.x converts it to `MCPError(INTERNAL_ERROR, "Server returned an error response")` (observed).

## Evidence

- OBSERVATION: on mcp 2.1.1, `streamable_http.py` maps every status ≥400 except 404 to `ErrorData(INTERNAL_ERROR, "Server returned an error response")`.
- MEASUREMENT: a loopback server returning 401, 403 or 500 gives the identical `str(err)` each time, and `'401' in str(err)` is False.
- MEASUREMENT: parent 0/1 vs candidate 1/1 on 401 re-consent; the 500 guard passes on both.
- MEASUREMENT: candidate `make test` is 216 + 153 + 22 passed.
- INFERENCE: the CIMD tool has the same defect (finding #2).
- DECISION: recommend the candidate for human review (draft PR). No merge.

## Reward-hack check (independent critic)

A separate subagent with no edit rights reviewed the diff. Its verdict was `CRITIC: CLEAR`. It reproduced the parent failure itself, confirmed that the existing tests are byte-identical, and confirmed that the stub drives the real MCPClient → httpx2 adapter → mcp session. It also checked the Authorization header arriving at the stub, robustness to bogus proxy env vars, and that a DEBUG log capture contains no token. Non-blocking gaps it named:

1. The `strands-agents>=1.30.0` floor is not proven to support `auth_provider`; 1.57.1, which deploys resolve today, does.
2. `httpx` was undeclared; this is fixed in the candidate.
3. A harmless strands RuntimeWarning appears after a failed init.
4. Strands warns about "plaintext http" against the loopback stub only.
5. A 401 after a successful init still does not re-ask for consent (unchanged behaviour).

## Security review

- Per-user token isolation is unchanged. `_BearerAuth` and `MCPClient` are created per call with no module or cached state, and the token still comes only from the caller's own workload token.
- The token is not placed in any exception text or log.
- Re-consent is triggered only by a real 401 from the configured MCP URL. A 500 or 403 does not trigger it, so a flaky server cannot spam users with connect links.
- No new network destinations, permissions, IAM, Slack or AWS calls are added.

## Next steps

1. Apply the same `_BearerAuth` to `cimd/tool.py`, through a shared helper, and add a loopback-stub test for CIMD 401 → forget connection + new link.
2. Raise the `strands-agents` floor to the first version with `MCPClient(auth_provider=...)`, or add a lock file, so error-shape contracts stop drifting silently.
3. Handle a 401 after initialize (mid-session revocation) by surfacing it from tool results. Measure first whether GitHub's MCP server ever does this.

## Witness

- Session commit: `ea7bfa6168a5e4e69c010c41e81f41e211475007`
- Report sha256 (this file with the Witness section body replaced by the single line `PENDING`): `dccdba671ae2b31f6783849cd57d3056bdff0f9e98235b026d52d7ca10fcb2b0`
- Witness stamp: `a37263bd9cc9c0b45fb822d66a0b01bd0e614a58f0952b94478a5571ac7f0ce5`

Verify:
1. `git show ea7bfa6` exists in the repo history.
2. Copy this file and replace everything after `## Witness` with a blank line, `PENDING`, and a trailing newline.
3. `sha256sum` the copy and compare it with the report sha256 above.
4. `printf '%s%s' <report sha256> ea7bfa6168a5e4e69c010c41e81f41e211475007 | sha256sum` gives the witness stamp.
5. Re-run the receipt: on the dream branch, `git checkout ea7bfa6 -- backends/agents/slack_agent/src/github.py`, then `uv run --quiet --no-project --python 3.13 --with-requirements backends/agents/slack_agent/requirements-dev.txt pytest -q backends/agents/slack_agent/tests/test_github_tool.py` (expect 1 failed, 6 passed); `git checkout HEAD -- backends/agents/slack_agent/src/github.py` and expect 7 passed.
