# Per-User Identity and Security

## The problem this app solves

A Slack app in a channel is used by **many people**. If the agent calls a third-party API like LinkedIn or GitHub with a single shared token, every user sees the profile of whoever connected first. That's a data leak.

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
- **In AWS, the agent doesn't trust the payload.** The `userId` field in the payload is used only locally. In the Runtime, the token that decides whose LinkedIn or GitHub data is returned comes from `runtimeUserId`.
- **The runtime is IAM-only:** no JWT authorizer and no public invoke path.

The AWS docs describe this pattern in [Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html) and [IAM permissions for AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).

## OAuth session binding

AgentCore requires your app to confirm that the person finishing consent is the person who started it ([docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)). Without that check, an attacker could send a victim their own consent link, and the victim's LinkedIn token would be stored under the attacker's user ID.

This app binds the session like this:

1. The worker stores `{nonce → runtime user, sessionUri, authorizationUrl}` in DynamoDB with a 10-minute TTL. The nonce is 256 bits, generated with `secrets.token_urlsafe(32)`.
2. It sends `…/oauth2/start?nonce=…` as a Slack **ephemeral** message, visible only to that user. The raw provider authorization URL (LinkedIn or GitHub) is never posted in Slack.
3. `/oauth2/start` sets an `HttpOnly; Secure; SameSite=Lax; Path=/oauth2` cookie containing the nonce, then redirects to LinkedIn.
4. `/oauth2/callback?session_id=…` requires:
   - the cookie to be present;
   - the nonce to exist and not be expired;
   - `record.sessionUri == session_id`, compared in constant time;
   - the nonce to be deleted successfully (a conditional delete, so it's single-use).

   Only then does it call `CompleteResourceTokenAuth(sessionUri, userId=record.runtime_user_id)`.

The unit tests in [test_oauth_callback.py](../backends/lambdas/tests/test_oauth_callback.py) cover each of these rejection paths.

**Residual risk:** anyone who gets the start link within its 10 minutes (for example if a user forwards it) can bind *their* account (LinkedIn or GitHub) to that user. The link is shown only to the requesting user and expires quickly. For stronger binding, have users sign in to your app with Slack before redirecting, and compare that signed-in identity to `record.slack_user`.

## CIMD providers: when we own the vault

Linear and Notion do not go through AgentCore Identity — no AgentCore client
authentication method fits their authorization servers, so this app is the OAuth client
itself ([cimd-providers.md](cimd-providers.md)). Two things change, and both matter here.

**The vault is ours.** Tokens live in the `cimd-tokens` DynamoDB table instead of the
AgentCore token vault: hash key `user_id` (`slack-<team>-<user>`), sort key `provider`, so
a lookup can only ever return the asking user's own token. Encryption at rest is on, the
`oauth-callback` role may only `PutItem`, the runtime role may read/write/delete, and a
`ttl` attribute drops connections unused for 90 days. Consider a customer-managed KMS key
in production so token reads appear in CloudTrail.

**The user ID comes from the payload, not the workload token.** The AgentCore Identity
tools pass the Runtime-injected workload access token to `GetResourceOauth2Token`, so the
request payload is never trusted. CIMD tools key their DynamoDB lookup on the `userId`
field of the payload instead. That value is still not user-supplied — the Runtime is
IAM-only, only the `agent-worker` Lambda role may invoke it, and that Lambda derives
`userId` from an HMAC-verified Slack event, exactly as it derives `runtimeUserId`. But the
assertion now comes from our worker rather than from AgentCore, so if anything else is
ever granted `InvokeAgentRuntime` on this runtime, that is the control to re-check.

**Binding is stronger, not weaker, on the browser side.** In addition to the cookie nonce,
the CIMD callback checks the OAuth `state` against the pending record and the `iss`
parameter (RFC 9207) against the issuer discovery resolved, and the PKCE verifier never
leaves AWS — only its SHA-256 hash travels through the browser. The rejection paths are
covered in [test_cimd_callback.py](../backends/lambdas/tests/test_cimd_callback.py).

**What leaves the account.** The sub-agent sends the user's request text to the remote MCP
server, so Slack content reaches that vendor. Keep `scope` minimal in the provider
registry; prefer read-only scopes unless the write flow is wanted.

## Files and links: reading what people share

The bot reads files people attach and pages they link to. Both bring outside content into a prompt that can call tools on the requester's accounts, and a link lets anyone make the agent send a request from inside AWS. The controls:

| Risk | Control | Where |
|---|---|---|
| Prompt injection from a file or page ("open an issue…") | Contents are sent as delimited data labelled "information, not instructions", like thread text. Only the latest message is a request, and write actions still need the requester's confirmation (`WRITE_RELAY_RULE`) | [main.py](../backends/agents/slack_agent/src/main.py), [thread_prompt.py](../backends/agents/slack_agent/src/thread_prompt.py), [web_fetch.py](../backends/agents/slack_agent/src/web_fetch.py) |
| Reading a file from elsewhere in Slack | `read_attachment` only opens file IDs the worker found in this thread; any other ID is refused before Slack is called | [attachments.py](../backends/agents/slack_agent/src/attachments.py) |
| Leaking the bot token | The download URL comes from Slack's `files.info`, never the payload, and must be `https://files.slack.com`. The token is an unredirected header, so it's dropped on any redirect, and only Slack hosts are followed | [slack_files.py](../backends/agents/slack_agent/src/slack_files.py) |
| SSRF: `fetch_url` to the metadata service or internal hosts | http/https on default ports only, no credentials in URLs. Every address a name resolves to must be public, on every redirect (at most 3). The connection goes to the checked address, so DNS rebinding can't switch it. No cookies or auth headers are sent | [web_fetch.py](../backends/agents/slack_agent/src/web_fetch.py) |
| Anonymous access to private SaaS pages | GitHub, Linear and Notion links are handed to that service's tool, so they're read with the requester's own token and permissions | `link_hosts` in [cimd/providers.py](../backends/agents/slack_agent/src/cimd/providers.py) |
| Oversized or hostile files | Type and size are checked from metadata before downloading, then again from Slack's answer and the bytes. A decoded image is capped at 50 megapixels. Converse limits (20 images, 5 documents) apply per invocation | [content_blocks.py](../backends/agents/slack_agent/src/content_blocks.py) |
| Storing people's files | Nothing is written to disk, S3 or logs. The Lambdas only ever see file metadata, and the agent holds the bytes for one invocation | — |

The `files:read` scope lets the bot read any file in a conversation it's a member of, which is the same reach as the `*:history` scopes it already has for messages.

## Other controls

| Control | Where |
|---|---|
| Slack tokens in Secrets Manager, never in Terraform state | `aws_secretsmanager_secret.slack` + `put-slack-secret.sh` |
| LinkedIn and GitHub client secrets never in Terraform state or on the command line | `identity-setup.sh` (temp file, mode 600) |
| CIMD providers have no client secret at all (public client, PKCE S256 + exact redirect URI) | [cimd_client.py](../backends/lambdas/src/slack_app/cimd_client.py) |
| Per-user CIMD tokens isolated by primary key, encrypted at rest, TTL-expired, write-only for the callback role | [data-stores.tf](../infra-as-code/tf-app/data-stores.tf), [lambdas.tf](../infra-as-code/tf-app/lambdas.tf) |
| Least-privilege roles: one per Lambda, scoped to its queue, table, secret, or runtime | [lambdas.tf](../infra-as-code/tf-app/lambdas.tf) |
| Runtime role limited to its allow-listed models (chat + MCP sub-agents), its credential providers (`oauth2_credential_provider_names`) and the CIMD token table | [agentcore-runtime/iam.tf](../infra-as-code/tf-modules/aws/agentcore-runtime/iam.tf), [tf-app/locals.tf](../infra-as-code/tf-app/locals.tf) |
| API throttling (20 rps steady, 40 burst by default) | `aws_apigatewayv2_stage.default` |
| Access logs without query strings (the nonce stays out of logs) | same |
| Encryption at rest | SQS SSE, DynamoDB SSE, ECR AES256 |
| Loop prevention: bot messages, edits, and Slack retries are ignored | [slack.py](../backends/lambdas/src/slack_app/slack.py) `should_handle` |
| No automatic retry of agent invocations (avoids double answers) | `agent_client.py` (`total_max_attempts=1`) and the worker (errors reported, not raised) |
| Containers run as non-root | Dockerfiles |
| Local-only code refuses to start in AWS | `local_server.py` requires `APP_ENV=local`; the in-memory queue and store are only used when `APP_ENV=local` |

## Data exposure to keep in mind

- LinkedIn, GitHub, Linear or Notion data is posted **in the thread where it was requested**. In a public channel, others can read it. DM the bot for private data.
- There is no stored conversation history. For every message, the worker reads the Slack thread and sends it to the agent, so anything posted in a thread (including the bot's answers from someone's connected accounts) becomes context for later messages in that thread, whoever sends them. Tool calls still only ever use the requester's own accounts, and the thread goes into the prompt as delimited data rather than as instructions.
- A file or page the bot reads is sent to Bedrock, like the thread is. An answer based on a file is posted in the thread, where everyone who can see the thread (and so the file) can read it.
- Local development uses real AWS credentials, copied into a Kubernetes Secret in your local cluster.

## When to add AgentCore Gateway

Add Gateway when you want many tools or targets behind one MCP endpoint, or when non-Slack clients will call the tools too. For per-user OAuth through Gateway, each call needs an **inbound JWT whose `sub` is the user**. That means either:
- users sign in through an IdP (for example Cognito authorization code flow) and you store their tokens against their Slack ID; or
- your backend issues its own signed JWTs with `sub = slack-<team>-<user>` and publishes a JWKS that Gateway's custom JWT authorizer trusts.

A machine-to-machine (client credentials) token has one `sub` for everyone, which brings back the shared-token problem.
