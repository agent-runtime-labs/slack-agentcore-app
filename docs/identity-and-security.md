# Per-User Identity and Security

## The problem this app solves

A Slack app in a channel is used by **many people**. If the agent calls LinkedIn with a single shared token, every user sees the profile of whoever connected first. That's a data leak.

AgentCore Identity stores third-party tokens in a **token vault** keyed by:

```
(workload identity)  +  (user ID)
```

So the question is how the user ID gets set, and whether it can be trusted.

## How this app sets the user ID

```
Slack event (signed) ──► team_id=T1, user=UALICE
        │
        ▼
λ agent-worker ── InvokeAgentRuntime(runtimeUserId="slack-T1-UALICE")      (IAM: InvokeAgentRuntimeForUser)
        │
        ▼
AgentCore Runtime ── mints a workload access token bound to "slack-T1-UALICE"
        │                and passes it to the agent (WorkloadAccessToken header)
        ▼
agent ── GetResourceOauth2Token(workloadIdentityToken=…) ──► vault entry for slack-T1-UALICE only
```

- **The user ID comes from a verified Slack event.** The `slack-events` Lambda checks Slack's HMAC signature and rejects requests older than 5 minutes before it trusts `team_id` or `user`.
- **Workspace-scoped:** `slack-<team>-<user>` avoids collisions, because Slack user IDs are only unique within one workspace.
- **Only one role can choose the user ID.** `bedrock-agentcore:InvokeAgentRuntimeForUser` is granted only to the worker Lambda's role, and only for this runtime. Any principal with that permission can act as any Slack user, so keep it that tight.
- **In AWS, the agent doesn't trust the payload.** The `userId` field in the payload is used only locally. In the Runtime, the token that decides whose LinkedIn data is returned comes from `runtimeUserId`.
- **The runtime is IAM-only:** no JWT authorizer and no public invoke path.

The AWS docs describe this pattern in [Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html) and [IAM permissions for AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).

## OAuth session binding

AgentCore requires your app to confirm that the person finishing consent is the person who started it ([docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)). Without that check, an attacker could send a victim their own consent link, and the victim's LinkedIn token would be stored under the attacker's user ID.

This app binds the session like this:

1. The worker stores `{nonce → runtime user, sessionUri, authorizationUrl}` in DynamoDB with a 10-minute TTL. The nonce is 256 bits, generated with `secrets.token_urlsafe(32)`.
2. It sends `…/oauth2/start?nonce=…` as a Slack **ephemeral** message, visible only to that user. The raw LinkedIn URL is never posted in Slack.
3. `/oauth2/start` sets an `HttpOnly; Secure; SameSite=Lax; Path=/oauth2` cookie containing the nonce, then redirects to LinkedIn.
4. `/oauth2/callback?session_id=…` requires:
   - the cookie to be present;
   - the nonce to exist and not be expired;
   - `record.sessionUri == session_id`, compared in constant time;
   - the nonce to be deleted successfully (a conditional delete, so it's single-use).

   Only then does it call `CompleteResourceTokenAuth(sessionUri, userId=record.runtime_user_id)`.

The unit tests in [test_oauth_callback.py](../backends/lambdas/tests/test_oauth_callback.py) cover each of these rejection paths.

**Residual risk:** anyone who gets the start link within its 10 minutes (for example if a user forwards it) can bind *their* LinkedIn account to that user. The link is shown only to the requesting user and expires quickly. For stronger binding, have users sign in to your app with Slack before redirecting, and compare that signed-in identity to `record.slack_user`.

## Other controls

| Control | Where |
|---|---|
| Slack tokens in Secrets Manager, never in Terraform state | `aws_secretsmanager_secret.slack` + `put-slack-secret.sh` |
| LinkedIn client secret never in Terraform state or on the command line | `identity-setup.sh` (temp file, mode 600) |
| Least-privilege roles: one per Lambda, scoped to its queue, table, secret, or runtime | [lambdas.tf](../infra-as-code/tf-app/lambdas.tf) |
| Runtime role limited to one model and one credential provider | [agentcore-runtime/iam.tf](../infra-as-code/tf-modules/aws/agentcore-runtime/iam.tf) |
| API throttling (20 rps steady, 40 burst by default) | `aws_apigatewayv2_stage.default` |
| Access logs without query strings (the nonce stays out of logs) | same |
| Encryption at rest | SQS SSE, DynamoDB SSE, ECR AES256 |
| Loop prevention: bot messages, edits, and Slack retries are ignored | [slack.py](../backends/lambdas/src/slack_app/slack.py) `should_handle` |
| No automatic retry of agent invocations (avoids double answers) | `agent_client.py` (`total_max_attempts=1`) and the worker (errors reported, not raised) |
| Containers run as non-root | Dockerfiles |
| Local-only code refuses to start in AWS | `local_server.py` requires `APP_ENV=local`; the in-memory queue and store are only used when `APP_ENV=local` |

## Data exposure to keep in mind

- LinkedIn data is posted **in the thread where it was requested**. In a public channel, others can read it. DM the bot for private data.
- Conversation history lives in memory inside the user's own Runtime session and disappears when the session ends.
- Local development uses real AWS credentials, copied into a Kubernetes Secret in your local cluster.

## When to add AgentCore Gateway

Add Gateway when you want many tools or targets behind one MCP endpoint, or when non-Slack clients will call the tools too. For per-user OAuth through Gateway, each call needs an **inbound JWT whose `sub` is the user**. That means either:
- users sign in through an IdP (for example Cognito authorization code flow) and you store their tokens against their Slack ID; or
- your backend issues its own signed JWTs with `sub = slack-<team>-<user>` and publishes a JWKS that Gateway's custom JWT authorizer trusts.

A machine-to-machine (client credentials) token has one `sub` for everyone, which brings back the shared-token problem.
