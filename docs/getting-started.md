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
| ngrok | 3.x | Optional: needed to connect a real Slack app locally, and to test the CIMD providers ([step 6](#6-try-linear-and-notion-cimd-providers)) — their authorization servers must be able to reach your machine. |
| direnv | — | Optional: auto-loads `.env` in your shell. |

AWS account requirements:
- Bedrock model access to **Claude Haiku 4.5** in `us-east-1` (the chat agent, triage and the GitHub/CIMD sub-agents all use it). Check with:
  ```bash
  aws bedrock-runtime converse --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
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
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`: optional, from a GitHub App (see [github-setup.md](github-setup.md)). Skip if you only want the LinkedIn tool.
- `CIMD_PROVIDERS` / `CIMD_TOKEN_TABLE`: optional, for Linear and Notion. **No client ID or secret exists for these** — see [step 6](#6-try-linear-and-notion-cimd-providers). Leave `CIMD_TOKEN_TABLE` empty for now and those tools stay switched off.
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
# -> same idea: add the printed URL to your GitHub App's
#    Callback URL (General settings). See github-setup.md.

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

## 6. Try Linear and Notion (CIMD providers)

LinkedIn and GitHub each needed a developer app, a client ID and a client secret. **Linear and Notion need none of that.** They support [CIMD](cimd-providers.md), where the app identifies itself with the URL of a small JSON document it publishes instead of a registration. So there is no developer portal to visit and no secret to store — but two other things have to be true, and both are new compared to steps 3–5.

### What these services are

Never used them? Neither is needed for anything else in this repo — a free account with a bit of content in it is enough to have something to ask about.

| | What it is | Sign up | Give it something to find |
|---|---|---|---|
| **Linear** | Issue tracker, in the same family as Jira but lighter. Work lives as *issues* inside *teams*, *projects* and *cycles* (sprints). | [linear.app](https://linear.app) — free plan, no card | Create a workspace, then 2–3 issues. Assign at least one to yourself so "my issues" returns something. |
| **Notion** | Docs / wiki / lightweight databases. Content lives as *pages*, which contain *blocks*. | [notion.so](https://notion.so) — free personal plan | Create a page with a recognisable title and a few lines of text. |

### What CIMD needs that the other providers didn't

1. **A DynamoDB table for tokens.** AgentCore Identity's vault holds the LinkedIn and GitHub tokens for you. With CIMD this app is the OAuth client, so it keeps the tokens itself.
2. **A public HTTPS base URL.** Linear's and Notion's authorization servers fetch our client metadata document *from their own servers*, so `http://localhost:8081` is unreachable to them. Locally that means an ngrok tunnel.

Until `CIMD_TOKEN_TABLE` is set, the `use_linear` and `use_notion` tools are simply not registered and nothing else changes.

### 6.1 Create a token table

If you have already deployed to AWS (`make deploy`), the table exists — get its name with:

```bash
./infra-as-code/tf-wrapper.sh dev output -raw cimd_token_table
```

Otherwise create a throwaway one for local development:

```bash
aws dynamodb create-table \
  --table-name slack-agentcore-local-cimd-tokens \
  --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=provider,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH AttributeName=provider,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

aws dynamodb update-time-to-live \
  --table-name slack-agentcore-local-cimd-tokens \
  --time-to-live-specification "Enabled=true,AttributeName=ttl"
```

Your local AWS profile is what the agent pod uses, so no extra IAM setup is needed.

### 6.2 Start the tunnel and point the app at it

The URL has to be public **before** Tilt starts, because the client document has to advertise the address it is actually served from.

```bash
ngrok http 8081          # or click ▶ on ngrok-tunnel in Tilt, then read the URL from its logs
```

Put both values in `.env`:

```bash
CIMD_PROVIDERS=linear,notion
CIMD_TOKEN_TABLE=slack-agentcore-local-cimd-tokens
PUBLIC_BASE_URL=https://<your-subdomain>.ngrok-free.app
```

`OAUTH2_RETURN_URL` is derived from `PUBLIC_BASE_URL`, so AgentCore Identity has to be told about the new callback too, or LinkedIn and GitHub will start failing:

```bash
set -a; source .env; set +a
LOCAL_OAUTH_RETURN_URL="$PUBLIC_BASE_URL/oauth2/callback" make local-workload
make up      # restart Tilt so the pods pick up the new config
```

> ngrok's free tier gives you a **new URL every restart**, and each change means editing `.env`, re-running `make local-workload` and restarting Tilt. If you plan to do this more than once, a reserved ngrok domain or a deployed dev stack is much less tedious.

### 6.3 Check what the authorization servers will see

This is the single most useful check, and it catches most CIMD problems before they happen:

```bash
curl -s "$PUBLIC_BASE_URL/oauth2/client-metadata.json" | jq
```

```json
{
  "client_id": "https://<your-subdomain>.ngrok-free.app/oauth2/client-metadata.json",
  "client_name": "Slack AgentCore Assistant",
  "client_uri": "https://<your-subdomain>.ngrok-free.app",
  "redirect_uris": ["https://<your-subdomain>.ngrok-free.app/oauth2/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "application_type": "web"
}
```

`client_id` **must** be the URL you just fetched — that match is what makes the document yours. If you get HTML instead of JSON, ngrok's browser interstitial is in the way; use a reserved domain or test against the deployed stack instead.

### 6.4 Ask a question

```bash
uv run --no-project python scripts/send_test_event.py --user UALICE --text "What are my open Linear issues?"
```

In the `slack-app` logs you'll see the same connect-link pattern as LinkedIn and GitHub, naming Linear:

```
[slack dry-run] chat.postEphemeral {… 'url': 'https://<your-subdomain>.ngrok-free.app/oauth2/start?nonce=…'}
[slack dry-run] chat.update {… 'text': '🔐 <@UALICE> I need access to your Linear account first. …'}
```

Open the link, approve, and you should land on **"Linear connected ✅"**. Ask the same question again and the answer comes from your real workspace.

Notion works identically with `--text "Search my Notion for <your page title>"`, with one thing to watch: **Notion's consent screen asks which pages to share.** Pick at least one, or every search comes back empty — which is correct behaviour, not a bug, and the agent is told to say "nothing was found" rather than "it does not exist".

### 6.5 Confirm the tokens are per-user

```bash
aws dynamodb get-item --table-name slack-agentcore-local-cimd-tokens \
  --key '{"user_id":{"S":"slack-TLOCALDEV1-UALICE"},"provider":{"S":"linear"}}' \
  --query 'Item.{scope:scope.S,issuer:issuer.S,expires:access_token_expires_at.N}'
```

One row per (user, provider). Ask the same question as `--user UBOB` and you get a fresh connect link — Bob cannot reach Alice's token, exactly as with the AgentCore Identity providers.

Adding a third CIMD service later (Sentry, Canva, …) is one entry in [`cimd/providers.py`](../backends/agents/slack_agent/src/cimd/providers.py) — no new account setup beyond signing in. See [cimd-providers.md](cimd-providers.md#adding-a-provider).

## 7. Try it with a real Slack workspace (optional)

1. Create a **separate dev Slack app** by following [slack-setup.md](slack-setup.md).
2. In `.env`, set `SLACK_DRY_RUN=false`, `SLACK_BOT_TOKEN=xoxb-…`, and `SLACK_SIGNING_SECRET=…`, then restart Tilt.
3. In Tilt, click ▶ on **ngrok-tunnel** and copy the `https://….ngrok-free.app` URL from its logs.
4. In the Slack app, set **Event Subscriptions → Request URL** to `https://<ngrok-host>/slack/events`.
5. Mention the bot in a channel or DM it. The "Connect LinkedIn" button still opens `http://localhost:8081`, which works because the browser is on the same machine.

## 8. Run the tests

```bash
make test
```

This runs 72 unit tests: signature checks, event filtering, the worker's auth branch, OAuth session binding and replay protection, the LinkedIn, GitHub and CIMD tools, CIMD discovery and callback handling, and the local server.

## 9. Stop

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
