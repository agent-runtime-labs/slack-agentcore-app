# OAuth and Identity SOTA Report — 2026

Dream Cycle 2026-10-02 · slot 2 · deep `oauth-and-identity` · scans `oauth-callback`, `cimd` · parent `90558ab3791de48e1da3bc2e9c672d2fb6c9408d`

## TL;DR

Last night's fix for GitHub (#39) left the same bug in the CIMD tool (Linear, Notion, Sentry and the other providers). When a user revokes the app on the provider's side, `cimd/tool.py` should forget the stored connection and send a new connect link, as `docs/cimd-providers.md:184` and `docs/testing-guide.md` §4c-g say. It never does. The tool checks `"401" in str(err)`, but on the installed stack (strands-agents 1.57.2, mcp 2.1.1) the MCP client gives a 401 the same generic text as a 500. The user gets `ERROR: could not reach Linear right now` on every request, and the dead token stays in DynamoDB.

The candidate moves last night's `_BearerAuth` hook out of `github.py` into a shared `bearer_auth.py`, and has `cimd/tool.py` use it. A loopback stub of the MCP server, driven through the real client stack, measures the effect: on a 401, forget-and-reconnect goes from **0/1 to 1/1**. On a 500, the connection is kept and the user gets an error with no consent prompt. Verdict: **ACCEPT**, meaning recommended for human review; nothing is promoted.

## Control plane (STEP 0.5)

| Capability | Available | Notes |
|---|---|---|
| uv | yes (`/root/.local/bin/uv`) | runs `make test` |
| make dream-check | yes | 5 slots, 2 ledger rows before tonight |
| make test | yes | parent: 216 + 153 + 22 = **391 passed** |
| terraform | no | `tf-fmt` blocked; tonight's diff touches no `.tf` |
| LLM API key | no | `LLM_EVAL=blocked`; the candidate needs no model calls (the stub fails at initialize, before any model is built) |
| GitHub MCP tools | yes | issue + draft PR; no gist tool, so `GIST=LOCAL` |
| Installed stack | strands-agents 1.57.2, mcp 2.1.1, httpx 0.28.1 | strands moved 1.57.1 → 1.57.2 since last night; the defect is unchanged |

## Ledger check and learning signals

There are 2 prior rows. 2026-09-29: issue #31 **CLOSED**, PR #32 **MERGED**. 2026-10-01: issue #38 **CLOSED** (completed), PR #39 **MERGED**. The merge rate is 2/2. No finding has repeated 3 times. `LLM_EVAL=blocked` is now a 3-night streak, so tonight's candidate again needs no model calls. Tonight picks up next step 1 from 2026-10-01, which falls inside tonight's `cimd` scan surface.

## Candidate findings (STEP 3)

Scored 1–5 on fit / novelty / testability / measurability / production value / reviewability.

| # | Finding | Evidence | Score | Picked |
|---|---|---|---|---|
| 1 | The CIMD tool never forgets a revoked connection on an MCP 401, because the error text has no status code | A: reproduced on the installed stack with a loopback 401 stub (parent returns `ERROR: could not reach Linear right now`, `store.deleted == []`) | 5/4/5/5/5/5 = 29 | **yes** |
| 2 | `oauth_callback.py:148-151` accepts a callback with no `iss`, even when the server advertised `authorization_response_iss_parameter_supported`. `discovery.py:112` computes `returns_iss`, but nothing reads it or passes it to the Lambda. RFC 9207 §2.4 says the client must reject a missing `iss` in that case | A: code read | 5/5/5/4/4/4 = 27 | no. It changes the pending-auth payload across two packages, so it is next step 1 |
| 3 | `_usable_token` (`tool.py:153-159`) deletes the connection on **any** refresh exception, including a timeout or a 5xx. The docs say only "refresh rejected" should do that | A: code read | 5/4/5/4/4/4 = 26 | no. Next step 2 |
| 4 | `_complete_cimd` (`oauth_callback.py:126-136`) burns the pending nonce on `?error=` before it checks `state`. A cross-site GET can cancel a user's sign-in in progress | A: code read; impact is denial of a single sign-in, and the user can ask again | 4/4/5/4/3/5 = 25 | no. Next step 3 |
| 5 | Discovery does not check that the metadata `issuer` equals the URL it was fetched from (RFC 8414 §3.3), or that the PRM `resource` equals `mcp_url` (RFC 9728 §3.3) | A: code read | 4/4/4/3/3/4 = 22 | no |

## Scan findings

**oauth-callback**
- Finding #2 (`iss` not enforced when advertised). Grade A.
- Finding #4 (the `?error=` branch consumes the nonce before the state check). Grade A.
- The private `/oauth2/start?nonce=` link is not bound to the person who opens it. Whoever opens a leaked link first can connect *their* account under the victim's runtime user. Grade C: this depends on how the link leaks, and it is DM-only today.
- `consume` deletes with only `attribute_exists`, so a record can expire between `get` and `consume`. The window is milliseconds. Grade C.

**cimd**
- Finding #1 (fixed in the candidate).
- Finding #3 (a 5xx during refresh deletes the connection). Grade A.
- Finding #5 (issuer and resource equality are not checked). Grade A.
- Concurrent turns that both refresh a rotating refresh token race: the loser's `invalid_grant` deletes the token the winner just stored. Grade C.
- Minor drift. `refresh` keeps `scope=""` when the response leaves it out, while the Lambda falls back to the requested scope. An `expires_in` of 60 seconds or less refreshes on every call (60s skew). The "unused for 90 days" TTL in the docs is really "not refreshed for 90 days". Grade A: code read.

## Competitors (context rows only; grade C, not re-researched tonight)

| Product | Re-auth behaviour relevant to tonight |
|---|---|
| Slack AI | First-party data only; no per-user third-party OAuth tool to re-consent |
| Amazon Q Developer in Slack | Uses AWS account / IAM identity, not a per-user third-party token vault |
| Glean | Admin-level connectors; per-user re-auth is not exposed in chat |
| Dust | Per-workspace OAuth connections with UI reconnect prompts (single-source, unverified) |

External basis for the change: the MCP authorization spec says resource servers answer invalid or expired tokens with HTTP 401 (grade B, recalled from the spec, not re-fetched tonight). The decision itself rests on the grade-A local reproduction.

## Frozen hypothesis (written before the candidate was evaluated; sha256 `f6d7b1d1…ac18f`)

> Given a CIMD provider's MCP endpoint answering HTTP 401 (revoked/expired user token), reproduced by a loopback HTTP stub driven through the real installed strands/mcp stack (no network, no AWS, no Bedrock), when cimd/tool.py detects the rejection from the HTTP status seen by an httpx.Auth hook (shared with github.py) instead of from the exception text, then the share of 401 cases that forget the stored connection and return AUTHORIZATION_REQUIRED with a fresh PKCE consent should rise from 0/1 on the parent to 1/1. It must hold that: a 500 still returns "ERROR: could not reach Linear right now" with the stored token kept (store.deleted == []) and no consent prompt; the stub sees "Bearer <this user's token>"; the GitHub 401/500 stub tests still pass; every existing test passes unmodified; no test is weakened, skipped or deleted.

## Candidate

- `src/bearer_auth.py` (new, 21 lines): `BearerAuth(httpx.Auth)`, moved unchanged from `github.py`'s `_BearerAuth`. It sets `Authorization: Bearer <token>` and records a 401 on the response.
- `src/github.py`: imports the shared class instead of defining it. There is no behaviour change, and the GitHub stub tests still pass.
- `src/cimd/tool.py`: `run_mcp_agent` passes `BearerAuth` as `auth_provider`, replacing raw `headers`. If initialize fails after a 401, it re-raises `MCPClientInitializationError("<Provider> rejected the access token (HTTP 401)")`. The caller `use_provider` is unchanged.
- `tests/test_cimd_tool.py`: adds a loopback MCP stub fixture and two tests, one for 401 (forget + consent) and one for 500 (keep + error). The 11 existing tests are byte-identical.
- `docs/testing-guide.md`: adds the missing `test_cimd_tool.py` row.

The diff is +99 / −29 lines across 5 files, one conceptual change.

## Evaluation receipt

The evaluator is `make test` plus the new stub tests. The corpus is the loopback stub answering 401 or 500.

| Run | `test_cimd_tool.py` | 401 → forget + consent | 500 → keep, error | make test |
|---|---|---|---|---|
| Parent `cimd/tool.py` + new tests (×3) | **1 failed, 12 passed** each time | **0/1**: returned `ERROR: could not reach Linear right now`, token kept | yes | 216 / 153+2 new (1 fail) / 22 |
| Candidate (×5 with `test_github_tool.py`) | **13 passed** (20 with GitHub) each time | **1/1** | yes | 216 / **155** / 22, all passed |

Effect: 401 forget-and-reconnect goes from 0/1 to 1/1, and tests go from 391 to 393 passing (+2 new, 0 changed or removed). The test is deterministic: every run gave the same result. `tf-fmt` is blocked (no terraform) and not relevant, since no `.tf` file changed. `LLM_EVAL=blocked`, but no model call is on this path.

## Darwin

Not run. The fix reuses an already-merged mechanism, so there is no meaningful mutation space. Failed lineage: none. One alternative was considered and rejected before any code was written: a copy of `_BearerAuth` inside `cimd/`. That would leave two copies of a status-detection contract that drifts with the mcp version.

## Evidence

- OBSERVATION: on mcp 2.1.1 the parent CIMD tool returns `ERROR: could not reach Linear right now` for a loopback 401, and `store.deleted == []`.
- MEASUREMENT: parent 0/1 vs candidate 1/1 on 401 forget-and-reconnect; the 500 guard passes on both.
- MEASUREMENT: candidate `make test` is 216 + 155 + 22 passed.
- INFERENCE: findings #2–#5 are code-read only; none was measured tonight.
- DECISION: recommend the candidate for human review (draft PR). No merge.

## Reward-hack check (independent critic)

A separate subagent with no edit rights reviewed the committed diff (`git diff 90558ab HEAD`). Its verdict was `CRITIC: CLEAR`.

- It reproduced the parent failure in a scratch copy: 1 failed, 19 passed. The candidate passes 20 of 20, 6 runs out of 6.
- It confirmed that the test diff only adds lines (59 insertions, 0 deletions), and that the stub drives the real `run_mcp_agent` → `MCPClient` → HTTP transport. It saw 2 requests, both with `Bearer at-1`.
- Across the four stub tests it captured 739 DEBUG log lines; the token and the word "Bearer" appear in none of them.
- It confirmed that the Dockerfile copies `src` wholesale with `PYTHONPATH=/app/src`, so `import bearer_auth` resolves in production.

Non-blocking gaps it named:

1. A 401 after a successful initialize, during a sub-agent tool call, still ends as an error and keeps the dead token. This is unchanged from the parent.
2. The old `"401" in str(err)` check is still in `use_provider`. Unrelated init text containing "401" would still delete the connection. This is pre-existing.
3. If a 401 is seen on one request and init then fails for another reason, the failure is reported as a 401. This is unlikely.
4. There is no 403 stub test pinning "403 does not delete".
5. Strands emits a harmless `_set_close_event never awaited` RuntimeWarning, also seen on the parent.

## Security review

- Per-user token isolation is unchanged. `BearerAuth` and `MCPClient` are created per call with no module or cached state. The token still comes only from this user's row (partition key = runtime user ID).
- The token is not placed in any exception text or log; the new message names only the provider.
- Deletion of a stored connection is triggered only by a real 401 from the provider's configured MCP URL. A 500 keeps the row, so a flaky server cannot wipe users' connections.
- No new network destinations, permissions, IAM, Slack or AWS calls are added.

## Next steps

1. Enforce RFC 9207: carry `returns_iss` into the pending-auth payload and reject a callback that has no `iss` when the server advertised support. Add a callback test with `iss` missing and `returns_iss=True`.
2. Delete a CIMD connection on refresh only for `invalid_grant` (HTTP 400/401 from the token endpoint), and keep it on a timeout or 5xx. Measure with a loopback token-endpoint stub (5xx → row kept).
3. Check `state` before burning the nonce on `?error=` in `_complete_cimd`.

## Witness

- Session commit: `90558ab3791de48e1da3bc2e9c672d2fb6c9408d`
- Report sha256 (this file with the Witness section body replaced by the single line `PENDING`): `a2078a4b520502f4713e0ef9f9a059a988930cfc6af22b5a40412d51816864a5`
- Witness stamp: `edae4b9b8b65bcb3f668de85964a52a83031bab72641ad15bcd15f86352626f3`

Verify:
1. `git show 90558ab` exists in the repo history.
2. Copy this file and replace everything after `## Witness` with a blank line, `PENDING`, and a trailing newline.
3. `sha256sum` the copy and compare it with the report sha256 above.
4. `printf '%s%s' <report sha256> 90558ab3791de48e1da3bc2e9c672d2fb6c9408d | sha256sum` gives the witness stamp.
5. Re-run the receipt: on the dream branch, `git checkout 90558ab -- backends/agents/slack_agent/src/cimd/tool.py`, then from `backends/agents/slack_agent` run `uv run --quiet --no-project --python 3.13 --with-requirements requirements-dev.txt pytest -q tests/test_cimd_tool.py` (expect 1 failed, 12 passed); `git checkout HEAD -- backends/agents/slack_agent/src/cimd/tool.py` and expect 13 passed.
