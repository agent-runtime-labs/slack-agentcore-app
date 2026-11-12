# Troubleshooting

## Local (Tilt)

| Symptom | Cause | Fix |
|---|---|---|
| `tilt up` refuses the cluster | The kube context isn't in `allow_k8s_contexts` | Switch to your local context, or add its name in the [Tiltfile](../Tiltfile). |
| `connection refused 127.0.0.1:6443` | Kubernetes is disabled in Rancher Desktop | Rancher Desktop → Preferences → Kubernetes → Enable. |
| Pods stuck in `ErrImagePull` | The cluster can't see locally built images | Rancher Desktop → Container Engine → **dockerd (moby)**. |
| Tiltfile error at `aws configure export-credentials` | Profile missing or SSO session expired | `aws sso login --profile …`, or fix `AWS_PROFILE` in `.env`. |
| `ExpiredTokenException` after a while | The Kubernetes Secret holds short-lived credentials | Restart Tilt. |
| `AccessDeniedException … GetWorkloadAccessTokenForUserId … Workload Identity does not belong to caller account` | The local workload identity doesn't exist yet (or is in another region or account) | `make local-workload`, with the same `AWS_REGION` as in `.env`. |
| `ResourceNotFoundException` for the credential provider | The provider wasn't created, or its name differs | `make identity` (LinkedIn) or `make identity-github` (GitHub); check `LINKEDIN_PROVIDER_NAME` / `GITHUB_PROVIDER_NAME`. |
| Browser shows *"Link expired"* after consent locally | `slack-app` reloaded (code change), which cleared the in-memory store | Ask the bot again for a new link. |
| `send-test-mention` returns 401 | Signing secret mismatch | The script and the pod both read `SLACK_SIGNING_SECRET`; restart Tilt after changing `.env`. |
| `docker buildx` fails with `/var/run/docker.sock` | buildx is pointing at the `default` builder | `docker buildx use rancher-desktop` (or your engine's builder). |

## Files and links

| Symptom | Cause | Fix |
|---|---|---|
| "this app isn't allowed to read files yet (it needs the files:read scope)" | The app was installed before `files:read` was added | Add the scope, **Reinstall to Workspace**, and store the new bot token ([slack-setup.md](slack-setup.md#files-and-links)). |
| "I don't have access to Slack files here" | The agent has no bot token. Locally, `SLACK_BOT_TOKEN` is empty in `.env`; in AWS, `SLACK_SECRET_ARN` isn't readable | Set the token and restart Tilt. In AWS, check the runtime role's `ReadSlackBotToken` statement. |
| Agent log: `Slack sent a web page instead of file …` | Slack returned its sign-in page, usually because of a missing scope or a token from another workspace | As above. The token must belong to the workspace the file is in. |
| "it isn't stored in Slack, so I can't download it" | The file is an external link (Google Drive and the like) or a Slack Connect copy | Download it and attach the file itself, or paste a public link. |
| A file-only message gets no reply | Triage decided it wasn't for the bot, or the event subscription is missing | @mention the bot or DM it. Check the `Triage says …` line in the worker log. |
| "I can only open public web pages" for a site you can reach | The name resolves to a private address from the runtime, or the URL uses a non-default port | This is deliberate (SSRF protection). Share the content as a file instead. |
| A page's answer says little | The page is rendered by JavaScript, so its HTML has almost no text | Paste the relevant text, or link to a static version (e.g. the raw README). |

## Diagrams

How it works: [diagrams.md](diagrams.md). The tool only exists when `DIAGRAM_RENDER_FUNCTION` is set, the worker sent the thread timestamp, and the agent has a bot token.

| Symptom | Cause | Fix |
|---|---|---|
| The bot answers in text and never draws | The tool isn't registered: `DIAGRAM_RENDER_FUNCTION` is empty on the runtime, the runtime is still on an old container, or the bot token is missing | Check `aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id> --query environmentVariables`. Make sure `diagram_renderer_enabled = true` was applied, and try a **new thread**. Locally, set `DIAGRAM_RENDER_FUNCTION` in `.env` and restart Tilt. |
| "the diagram renderer is unavailable right now" | The agent couldn't invoke the renderer, or it crashed. The agent log has `Diagram renderer could not be reached` or `Diagram renderer failed: <type>` | Look at `/aws/lambda/<prefix>-diagram-renderer`. `AccessDenied` means the runtime role lacks `lambda:InvokeFunction` on it (`apply` again). A timeout or out-of-memory on the first call means raising `memory_size` in [diagrams.tf](../infra-as-code/tf-app/diagrams.tf). |
| Works after a pause but the first diagram is slow | Cold start: Chromium is unpacked on the first render | Expected: about 5–10 s. Later renders in the same container take about a second. |
| "the Mermaid source is invalid: Parse error on line …" | The model wrote bad Mermaid. The error goes back to it so it can retry | Usually fixed on the next attempt. If it keeps failing, ask for a simpler diagram: at most 15 nodes, short labels, quote labels with punctuation. `xychart-beta` is the most fragile type. |
| "this app isn't allowed to post files yet (it needs the files:write scope)" | The app was installed before `files:write` was added | Add the scope, **Reinstall to Workspace**, and store the new bot token ([slack-setup.md](slack-setup.md)). |
| "I'm not a member of this channel, so I can't post files here" | `not_in_channel` from Slack | `/invite @AgentCore Assistant` in that channel. A DM works without inviting. |
| "the diagram is too large; draw a smaller one" | The source is over the limit, or the PNG is over 4 MB | Ask for the top N or an aggregate: [limits](diagrams.md#limits). |
| Chart lines or bars are very faint, or labels vanish in dark mode | An older build: the default Mermaid theme with a transparent background | Deploy the current renderer ([index.mjs](../backends/diagram_renderer/index.mjs) sets an opaque background and a stronger palette). |
| `terraform apply` rebuilds the renderer image every time | A file in `backends/diagram_renderer` changed, such as an installed `node_modules/` | The tag is a hash of that folder. Remove `node_modules/` there. It is in `.gitignore`. |
| Image build fails at the Chromium download | The checksum in the Dockerfile doesn't match, or GitHub is unreachable | A changed upstream file fails the build on purpose. Update `CHROMIUM_VERSION` and `CHROMIUM_PACK_SHA256` together, from the release page. |

## LinkedIn / GitHub / AgentCore Identity

| Symptom | Cause | Fix |
|---|---|---|
| LinkedIn shows *"The redirect_uri does not match"* | The AgentCore callback URL isn't registered in LinkedIn | `./infra-as-code/scripts/identity-setup.sh show`, then add the URL under LinkedIn → Auth. |
| LinkedIn shows *"unauthorized_scope_error"* | The OpenID Connect product isn't enabled | Request **Sign In with LinkedIn using OpenID Connect** under Products. |
| GitHub shows *"The redirect_uri MUST match the registered callback URL"* | The AgentCore callback URL isn't registered on the GitHub App | `./infra-as-code/scripts/identity-setup.sh show`, then set it as the app's **Callback URL** (General settings). |
| GitHub shows *"This GitHub App is not installed on your account/organization"* or the bot can't see a repo/org | The org owner hasn't installed the GitHub App yet | Have an org owner install it from `https://github.com/apps/<app-slug>` — see [github-setup.md](github-setup.md#4-install-the-app-on-each-organization). |
| AgentCore error about the return URL | `/oauth2/callback` isn't allow-listed on the workload identity | Local: `make local-workload`. AWS: `terraform apply` (re-runs `allowed_return_urls`). This allow-list is shared by both providers — one fix covers LinkedIn and GitHub. |
| *"Sign-in not recognised"* page | The browser doing consent isn't the one that opened the link, or cookies are blocked | Open the Slack link and finish consent in the same browser. Allow cookies for the API domain. |
| The bot asks to connect again every time | Consent never completed (callback not reached) | Check the `oauth-callback` logs. The user must land on "LinkedIn connected ✅" or "GitHub connected ✅". |

## CIMD providers (Linear, Notion, …)

| Symptom | Cause | Fix |
|---|---|---|
| `use_linear` / `use_notion` never appears | `CIMD_TOKEN_TABLE` is empty, or the key isn't in `cimd_providers` | The agent logs `CIMD tools enabled: …` at startup. Locally, set `CIMD_TOKEN_TABLE` to a deployed table — see [cimd-providers.md](cimd-providers.md#local-development). |
| Consent screen shows `invalid_client` | The authorization server couldn't fetch our client metadata document, or `client_id` inside it doesn't match its URL | `curl "$(./infra-as-code/tf-wrapper.sh dev output -raw cimd_client_id)"` **from outside your network** and compare the `client_id` field to the URL. |
| `invalid_request` about `redirect_uri` | `OAUTH2_RETURN_URL` isn't in `redirect_uris` | Both derive from `PUBLIC_BASE_URL`; if they disagree, one is stale (common after an ngrok restart). |
| Agent log: `CimdUnsupported` | The server advertises `client_id_metadata_document_supported: false` | It needs DCR or a pre-registered client and cannot be a CIMD provider. |
| Agent log: `does not support PKCE S256` | The server offers only `plain` | We refuse to send a plaintext challenge from a public client. |
| Agent log: HTTP 403 during discovery | The provider's CDN blocked the request | We send an explicit `User-Agent` for this reason ([_http.py](../backends/agents/slack_agent/src/cimd/_http.py)); check the provider isn't geo/IP blocking the runtime. |
| User must reconnect on every question | No refresh token was issued, or refreshes are rejected | Check `scope` in the registry — some servers only return refresh tokens for particular scopes. |

## AWS

| Symptom | Cause | Fix |
|---|---|---|
| Slack says *"Your URL didn't respond with the value of the challenge parameter"* | The Slack secret is still empty, so signature checks fail | Run `put-slack-secret.sh`, then retry verification. |
| Bot shows "🤔 Thinking…" forever | The worker failed before it could update the message | `aws logs tail /aws/lambda/<prefix>-agent-worker`; check the DLQ (`processing_dlq_url` output). |
| `AccessDeniedException` on `InvokeAgentRuntime` | Missing `InvokeAgentRuntimeForUser` permission | Both actions are required when `runtimeUserId` is set; see [lambdas.tf](../infra-as-code/tf-app/lambdas.tf). |
| `oauth-callback` returns *"Something went wrong"*; logs show `AccessDeniedException ... CompleteResourceTokenAuth ... Access denied when retrieving secret 'bedrock-agentcore-identity!default/oauth2/<provider>-...'` | `CompleteResourceTokenAuth` runs as the calling Lambda's IAM role, and that role needs `secretsmanager:GetSecretValue` on the AgentCore-managed secret to finish the token exchange — a permission distinct from `bedrock-agentcore:CompleteResourceTokenAuth` itself | Grant `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:<region>:<account>:secret:bedrock-agentcore-identity!default/oauth2/*` to the `oauth-callback` Lambda's role (see [lambdas.tf](../infra-as-code/tf-app/lambdas.tf)); this is already wired in, but re-check it after adding a third provider. |
| Runtime logs show `AccessDeniedException` on `GetResourceOauth2Token` for a newly added provider (e.g. GitHub) | The Runtime execution role's IAM policy only listed the old provider name(s) | Confirm `oauth2_credential_provider_names` in [agent-runtime.tf](../infra-as-code/tf-app/agent-runtime.tf) includes the new provider, then `terraform apply`. |
| Runtime logs show `AccessDenied` for `bedrock:InvokeModel` | The model ID changed without updating IAM, or model access isn't enabled | Apply Terraform again; enable model access in the Bedrock console. |
| Lambda create fails: *image manifest … not supported* | The image was pushed with attestations | The build script uses `--provenance=false --sbom=false`; rebuild with it. |
| Duplicate answers | Slack retries, or SQS redelivery after a timeout | Retries are ignored by design; make sure the worker timeout (150s) is below the queue visibility timeout (900s). |

## Useful commands

```bash
# Tilt
tilt logs slack-app -f
kubectl -n slack-agentcore get pods

# AgentCore Identity
./infra-as-code/scripts/identity-setup.sh show
aws bedrock-agentcore-control list-workload-identities
aws bedrock-agentcore-control list-oauth2-credential-providers

# AWS deployment
./infra-as-code/tf-wrapper.sh dev output
aws logs tail /aws/lambda/slack-agentcore-dev-slack-events --since 10m
aws sqs get-queue-attributes --queue-url "$(./infra-as-code/tf-wrapper.sh dev output -raw processing_dlq_url)" \
  --attribute-names ApproximateNumberOfMessages
```
