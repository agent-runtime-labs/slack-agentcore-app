# Deploying to AWS (Terraform)

## What gets created

`terraform plan` for `dev` creates about **40 resources**:

| Area | Resources |
|---|---|
| Images | 2 ECR repositories (`-agent`, `-lambdas`) with lifecycle policies. `docker buildx` builds and pushes linux/arm64 images from your machine. |
| Agent | AgentCore Runtime `slack_agentcore_dev` plus its execution role, and the allowed return URL on its workload identity. |
| Slack path | HTTP API (`POST /slack/events`, `GET /oauth2/start`, `GET /oauth2/callback`, `GET /oauth2/client-metadata.json`) with throttling and access logs; 3 Lambdas; SQS FIFO queue and DLQ |
| State | DynamoDB `pending-oauth` table (TTL); DynamoDB `cimd-tokens` table (per-user CIMD tokens, TTL); an empty Secrets Manager secret for Slack credentials |

Not managed by Terraform: the LinkedIn and GitHub OAuth2 credential providers (see [linkedin-setup.md](linkedin-setup.md) and [github-setup.md](github-setup.md)).

**The CIMD providers (Linear, Notion) need no setup at all** — no developer app, no client secret, no credential provider. `terraform apply` publishes the client metadata document at a stable HTTPS URL and creates the token table, which is everything they require. Locally they needed an ngrok tunnel; on AWS they just work. Control which ones are enabled with `cimd_providers` in the tfvars (`[]` turns them off).

## Prerequisites

- You've done the local setup in [getting-started.md](getting-started.md), at least step 3 (`make identity`).
- Docker is running (Terraform builds the images) and Terraform is ≥ 1.11.
- You have an S3 bucket for Terraform state.

## Steps

### 1. Point the backend at your state bucket

Edit [infra-as-code/tf-vars/dev/backend.tf](../infra-as-code/tf-vars/dev/backend.tf):

```hcl
bucket       = "my-tf-state-bucket"
key          = "slack-agentcore-app/dev/terraform.tfstate"
region       = "us-east-1"
use_lockfile = true      # S3-native locking, no DynamoDB table needed
```

Review [app-infra-params.tfvars](../infra-as-code/tf-vars/dev/app-infra-params.tfvars). `linkedin_provider_name` and `github_provider_name` must match the providers you created. `cimd_providers` defaults to `["linear", "notion"]`; nothing else is needed for those.

### 2. Deploy

```bash
export AWS_PROFILE=personal AWS_REGION=us-east-1
cd infra-as-code
./tf-wrapper.sh dev init
./tf-wrapper.sh dev plan
./tf-wrapper.sh dev apply
```

The first apply takes about 5–10 minutes, most of it building images. Later applies rebuild an image only when files under its source folder change, because the image tag is a hash of those files.

### 3. Store the Slack credentials

```bash
SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=... ./scripts/put-slack-secret.sh dev
```

### 4. Connect Slack

```bash
./tf-wrapper.sh dev output -raw slack_events_url
```

Set that URL as the Request URL of your **AWS** Slack app ([slack-setup.md](slack-setup.md)) and wait for **Verified ✓**.

### 5. Smoke test in Slack

Do these in a DM with the bot, in order. Each one adds a piece: plain chat, then AgentCore
Identity consent, then CIMD consent.

1. **`hello`** — an answer within a few seconds. Proves Slack → API Gateway → SQS → worker →
   Runtime → Bedrock works end to end.
2. **`what's my LinkedIn name?`** — a private **Connect LinkedIn** button; connect, then ask
   again and you get your name.
3. **`what's my GitHub username?`** — same flow, **Connect GitHub**.
4. **`what are my open Linear issues?`** — same flow, **Connect Linear**. This one is CIMD:
   no client secret was involved anywhere. Ask again after connecting.
5. **`search my Notion for <a page title>`** — **Connect Notion**. Its consent screen asks
   *which pages to share*; pick at least one, or searches legitimately return nothing.
6. **Ask a teammate to try steps 2–5.** They get their own connect buttons and never see
   your data — that's the whole point of the per-user design.

Before step 4, confirm the CIMD client document is publicly reachable — this is the one
thing that is new on AWS and it takes a second to check:

```bash
curl -s "$(./tf-wrapper.sh dev output -raw cimd_client_id)" | jq
# client_id in the response must equal the URL you just fetched
```

Then watch the logs:

```bash
aws logs tail /aws/lambda/slack-agentcore-dev-agent-worker --follow
aws logs tail /aws/lambda/slack-agentcore-dev-oauth-callback --follow
aws logs tail /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT --follow
```

Full step-by-step expectations for each flow are in [testing-guide.md § 7](testing-guide.md#7-deployed-to-aws).

## Updating

- **Code:** edit, then `./tf-wrapper.sh dev apply`. The changed image is rebuilt and the functions and runtime are updated.
- **Model:** set `model_id` (chat) or `triage_model_id` in the tfvars (for example `us.amazon.nova-pro-v1:0`), then apply. IAM follows automatically. Cheaper models are more likely to misjudge when to answer, ask or stay quiet.
- **CI-built images:** pass `-var agent_image_tag=<tag> -var lambda_image_tag=<tag>` to skip local builds.

## Other environments

Copy `tf-vars/dev` to `tf-vars/prod`, change `env`, the backend `key`, and the tags, then run `./tf-wrapper.sh prod …`. Use a separate Slack app for each environment.

## Tear down

```bash
./tf-wrapper.sh dev destroy
```

The ECR repositories are force-deleted, and the `cimd-tokens` table goes with the stack — every user's Linear and Notion connection disappears and they will be asked to connect again after the next deploy. The Slack secret is deleted immediately in `dev`; in `prod` it has a 30-day recovery window. The LinkedIn and GitHub credential providers and the local workload identity are left in place; see [getting-started.md](getting-started.md#9-stop) to delete them.

## Cost notes (us-east-1, light usage)

| Item | Driver |
|---|---|
| Claude Haiku 4.5 | $1 per 1M input tokens / $5 per 1M output tokens. Every channel message the bot can see gets a triage call, which includes the thread so far (up to 31 messages), and each answer includes it too. In busy channels with long threads, triage is the larger share. |
| AgentCore Runtime | Billed per second of CPU and memory while a session is active. Sessions idle out after 5 min (`idle_session_timeout_seconds = 300`) and are hard-capped at 1 hour (`max_session_lifetime_seconds = 3600`). |
| Lambda, API Gateway, SQS, DynamoDB | Pay-per-request; effectively free-tier at sandbox volume. |
| ECR | Storage for up to 10 images per repository. |
| Secrets Manager | $0.40/month per secret: the Slack secret plus the LinkedIn and GitHub client secrets created by AgentCore Identity. |
| CloudWatch Logs | 30-day retention. |

Check current prices on the AWS pricing pages before relying on these.
