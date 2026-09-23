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
