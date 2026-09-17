# Deploying to AWS (Terraform)

## What gets created

`terraform plan` for `dev` creates **40 resources**:

| Area | Resources |
|---|---|
| Images | 2 ECR repositories (`-agent`, `-lambdas`) with lifecycle policies. `docker buildx` builds and pushes linux/arm64 images from your machine. |
| Agent | AgentCore Runtime `slack_agentcore_dev` plus its execution role, and the allowed return URL on its workload identity. |
| Slack path | HTTP API (`POST /slack/events`, `GET /oauth2/start`, `GET /oauth2/callback`) with throttling and access logs; 3 Lambdas; SQS FIFO queue and DLQ |
| State | DynamoDB `pending-oauth` table (TTL); an empty Secrets Manager secret for Slack credentials |

Not managed by Terraform: the LinkedIn and GitHub OAuth2 credential providers (see [linkedin-setup.md](linkedin-setup.md) and [github-setup.md](github-setup.md)).

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

Review [app-infra-params.tfvars](../infra-as-code/tf-vars/dev/app-infra-params.tfvars). `linkedin_provider_name` and `github_provider_name` must match the providers you created.

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

### 5. Smoke test

1. DM the bot `hello`. You should get an answer within a few seconds.
2. Ask `what's my LinkedIn name?`. You should get a private **Connect LinkedIn** button; connect, then ask again.
3. Ask `what's my GitHub username?`. Same flow, with a **Connect GitHub** button.
4. Check the logs:
   ```bash
   aws logs tail /aws/lambda/slack-agentcore-dev-agent-worker --follow
   aws logs tail /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT --follow
   ```

## Updating

- **Code:** edit, then `./tf-wrapper.sh dev apply`. The changed image is rebuilt and the functions and runtime are updated.
- **Model:** set `model_id` in the tfvars (for example `us.amazon.nova-lite-v1:0`), then apply. IAM follows automatically.
- **CI-built images:** pass `-var agent_image_tag=<tag> -var lambda_image_tag=<tag>` to skip local builds.

## Other environments

Copy `tf-vars/dev` to `tf-vars/prod`, change `env`, the backend `key`, and the tags, then run `./tf-wrapper.sh prod …`. Use a separate Slack app for each environment.

## Tear down

```bash
./tf-wrapper.sh dev destroy
```

The ECR repositories are force-deleted. The Slack secret is deleted immediately in `dev`; in `prod` it has a 30-day recovery window. The LinkedIn and GitHub credential providers and the local workload identity are left in place; see [getting-started.md](getting-started.md#8-stop) to delete them.

## Cost notes (us-east-1, light usage)

| Item | Driver |
|---|---|
| Nova Micro | $0.035 per 1M input tokens / $0.14 per 1M output tokens: a few cents for hundreds of messages. |
| AgentCore Runtime | Billed per second of CPU and memory while a session is active. Sessions idle out after 5 min (`idle_session_timeout_seconds = 300`) and are hard-capped at 1 hour (`max_session_lifetime_seconds = 3600`). |
| Lambda, API Gateway, SQS, DynamoDB | Pay-per-request; effectively free-tier at sandbox volume. |
| ECR | Storage for up to 10 images per repository. |
| Secrets Manager | $0.40/month per secret: the Slack secret plus the LinkedIn and GitHub client secrets created by AgentCore Identity. |
| CloudWatch Logs | 30-day retention. |

Check current prices on the AWS pricing pages before relying on these.
