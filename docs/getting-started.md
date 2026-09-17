# Getting Started (Local Setup)

This guide gets the whole app running on your laptop with **Tilt** on a local Kubernetes cluster. Bedrock and AgentCore Identity are real AWS services, so you still need an AWS account. Everything else runs locally.

## 1. Prerequisites

| Tool | Tested version | Notes |
|---|---|---|
| Rancher Desktop (or Docker Desktop / OrbStack) | — | **Kubernetes enabled** and container engine set to **dockerd (moby)**, so Tilt-built images are visible to the cluster without a registry. |
| Tilt | 0.37 | `brew install tilt` |
| kubectl | 1.36 | Current context must be your local cluster (`kubectl config current-context`). |
| uv | 0.11 | Runs the tests and helper scripts. |
| AWS CLI v2 | 2.35 | Profile with Bedrock and AgentCore permissions. |
| Terraform | ≥ 1.11 | Only needed to deploy to AWS. |
| ngrok | 3.x | Optional: only needed to connect a real Slack app locally. |
| direnv | — | Optional: auto-loads `.env` in your shell. |

AWS account requirements:
- Bedrock model access to **Amazon Nova Micro** in `us-east-1`. Check with:
  ```bash
  aws bedrock-runtime converse --model-id us.amazon.nova-micro-v1:0 \
    --messages '[{"role":"user","content":[{"text":"Say OK"}]}]' \
    --query 'output.message.content[0].text' --output text
  ```
- Permissions to call `bedrock:InvokeModel` and `bedrock-agentcore:*` (Identity APIs). An admin profile is fine for a sandbox.

## 2. Configure

```bash
cd slack-agentcore-app
cp .env.tmpl .env
```

Edit `.env`:
- `AWS_PROFILE` / `AWS_REGION`: the profile and region to use.
- `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET`: from your LinkedIn developer app (see [linkedin-setup.md](linkedin-setup.md)).
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`: optional, from a GitHub OAuth App (see [github-setup.md](github-setup.md)). Skip if you only want the LinkedIn tool.
- Leave `SLACK_DRY_RUN=true` for now. You don't need a Slack workspace yet.

`.env` is git-ignored. Never commit it.

## 3. Create the AgentCore Identity resources (one time)

```bash
set -a; source .env; set +a      # or: direnv allow

# LinkedIn OAuth2 credential provider (built-in LinkedIn vendor)
make identity
# -> prints a redirect URL like
#    https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/<uuid>
#    Add it to LinkedIn app > Auth > Authorized redirect URLs.

# GitHub OAuth2 credential provider (optional, built-in GitHub vendor)
make identity-github
# -> same idea: add the printed URL to your GitHub OAuth App's
#    Authorization callback URL. See github-setup.md.

# Workload identity used only by local development
make local-workload
```

The local workload identity allows `http://localhost:8081/oauth2/callback` as a return URL. That's where AgentCore Identity sends your browser after LinkedIn or GitHub consent.

## 4. Start the stack

```bash
make up          # = tilt up
```

Open the Tilt UI at <http://localhost:10350>. You should see:

| Resource | What it is | Port |
|---|---|---|
| `slack-agent` | Strands agent (same image as AgentCore Runtime, `localdev` target) | `localhost:8080` |
| `slack-app` | Lambda handlers behind FastAPI (`/slack/events`, `/oauth2/*`) | `localhost:8081` |
| `unit-tests` | Runs both test suites whenever the source changes | — |
| `send-test-mention` | Button: sends a signed fake Slack mention | — |
| `ngrok-tunnel` | Button: exposes `:8081` for a real Slack app | inspector at `:4040` |

Edits under `backends/**/src` sync into the pods and restart them automatically.

> Tilt copies **short-lived credentials** from `aws configure export-credentials` into a Kubernetes Secret when it starts. If you use SSO or assumed roles and they expire, restart Tilt (`Ctrl+C`, then `tilt up`).

## 5. Try it without Slack

1. In Tilt, click ▶ on **send-test-mention**, or run:
   ```bash
   uv run --no-project python scripts/send_test_event.py --text "What is my LinkedIn name?"
   ```
2. Open the **slack-app** logs. Because this is your first LinkedIn question, you'll see:
   ```
   [slack dry-run] chat.postEphemeral {... 'url': 'http://localhost:8081/oauth2/start?nonce=...'}
   [slack dry-run] chat.update {... 'text': '🔐 <@ULOCALDEV1> I need access to your LinkedIn account first...'}
   ```
3. Copy the `http://localhost:8081/oauth2/start?nonce=…` link into your browser, sign in to LinkedIn, and approve. You should land on **"LinkedIn connected ✅"**.
4. Send the same question again. The log now shows `chat.update` with your real LinkedIn name.
5. Send it with a different user, `--user USOMEONEELSE`. That user is asked to connect again: tokens are stored per user.

If you set up `make identity-github`, the same flow works for `--text "What is my GitHub username?"` — same connect-link pattern, `get_my_github_profile` tool, `slack-agent-github` provider.

To call the agent container directly, use [backends/requests.http](../backends/requests.http) (VS Code REST Client) or curl:

```bash
curl -s localhost:8080/invocations -H 'Content-Type: application/json' \
  -H 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-session-000000000000000000000001' \
  -d '{"prompt":"Hello!","userId":"slack-TLOCALDEV1-ULOCALDEV1","sessionId":"local-session-000000000000000000000001"}'
```

## 6. Try it with a real Slack workspace (optional)

1. Create a **separate dev Slack app** by following [slack-setup.md](slack-setup.md).
2. In `.env`, set `SLACK_DRY_RUN=false`, `SLACK_BOT_TOKEN=xoxb-…`, and `SLACK_SIGNING_SECRET=…`, then restart Tilt.
3. In Tilt, click ▶ on **ngrok-tunnel** and copy the `https://….ngrok-free.app` URL from its logs.
4. In the Slack app, set **Event Subscriptions → Request URL** to `https://<ngrok-host>/slack/events`.
5. Mention the bot in a channel or DM it. The "Connect LinkedIn" button still opens `http://localhost:8081`, which works because the browser is on the same machine.

## 7. Run the tests

```bash
make test
```

This runs 39 unit tests: signature checks, event filtering, the worker's auth branch, OAuth session binding and replay protection, the LinkedIn and GitHub tools, and the local server.

## 8. Stop

```bash
make down        # removes the pods; the namespace and AWS identity resources remain
```

To remove the AgentCore Identity resources as well:

```bash
aws bedrock-agentcore-control delete-workload-identity --name slack-agent-local
aws bedrock-agentcore-control delete-oauth2-credential-provider --name slack-agent-linkedin
aws bedrock-agentcore-control delete-oauth2-credential-provider --name slack-agent-github
```

Next: [testing-guide.md](testing-guide.md) for every test flow with expected output, then [deployment.md](deployment.md) to run it in AWS.
