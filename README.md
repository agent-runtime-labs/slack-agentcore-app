# Slack × Amazon Bedrock AgentCore

A Slack assistant running on **Amazon Bedrock AgentCore Runtime** (Strands Agents + **Amazon Nova Micro**) that can read **each user's own LinkedIn and GitHub profile** through **AgentCore Identity** (OAuth2 authorization code grant, stored per user).

Based on the AWS blog post [Integrating Amazon Bedrock AgentCore with Slack](https://aws.amazon.com/blogs/machine-learning/integrating-amazon-bedrock-agentcore-with-slack/) and its [sample](https://github.com/aws-samples/sample-Integrating-Amazon-Bedrock-AgentCore-with-Slack). This version uses Terraform, Python Lambdas, Tilt for local development, and per-user outbound OAuth.

```
Slack ─► API Gateway ─► λ slack-events ─► SQS FIFO ─► λ agent-worker ─► AgentCore Runtime ─► Nova Micro
                                                         │  runtimeUserId=slack-<team>-<user>      │
                                                         │                                          ├─► AgentCore Identity ─► LinkedIn
                                                         │                                          └─► AgentCore Identity ─► GitHub
Browser ─► API Gateway ─► λ oauth-callback (session binding) ─► CompleteResourceTokenAuth
```

## Repository layout

```
.
├── backends/
│   ├── agents/slack_agent/     Strands agent (AgentCore Runtime container)
│   ├── lambdas/                slack-events, agent-worker, oauth-callback (one image) + local server
│   └── requests.http           Handy local HTTP requests
├── infra-as-code/
│   ├── tf-app/                 Root Terraform stack
│   ├── tf-modules/aws/         container-image, agentcore-runtime, lambda-function
│   ├── tf-vars/<env>/          backend.tf + app-infra-params.tfvars per environment
│   ├── k8s/tilt/               Kubernetes manifests used by Tilt
│   ├── scripts/                identity-setup.sh, put-slack-secret.sh
│   └── tf-wrapper.sh           ./tf-wrapper.sh <env> <init|plan|apply|destroy|output|validate>
├── scripts/send_test_event.py  Signed fake Slack events for local testing
├── docs/                       Guides (below)
├── Tiltfile  Makefile  .env.tmpl
```

## Quick start (local)

```bash
cp .env.tmpl .env                          # set AWS_PROFILE, LINKEDIN_CLIENT_ID/SECRET
set -a; source .env; set +a
make identity local-workload                # AgentCore Identity: LinkedIn provider + local workload identity
make identity-github                        # optional: GitHub provider (needs GITHUB_CLIENT_ID/SECRET)
make up                                     # tilt up -> http://localhost:10350
```

Then click **send-test-mention** in Tilt and follow the connect link that appears in the `slack-app` logs.

## Documentation

| Guide | |
|---|---|
| [Getting started (local setup)](docs/getting-started.md) | Prerequisites, Tilt, testing without Slack |
| [Testing guide](docs/testing-guide.md) | Step-by-step test flows with expected output (local and AWS) |
| [Architecture](docs/architecture.md) | Component and sequence diagrams (Mermaid), design choices |
| [Slack setup](docs/slack-setup.md) | Create the Slack app from a [manifest](docs/slack-app-manifest.yaml) |
| [LinkedIn & AgentCore Identity setup](docs/linkedin-setup.md) | Developer app, credential provider, redirect URLs |
| [GitHub & AgentCore Identity setup](docs/github-setup.md) | Same pattern as LinkedIn, using the `GithubOauth2` vendor |
| [Deployment](docs/deployment.md) | Terraform with the S3 backend, updating, teardown, cost notes |
| [Per-user identity & security](docs/identity-and-security.md) | Why tokens don't leak between users; session binding |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and fixes |

## Common commands

```bash
make test          # 39 unit tests (lambdas + agent)
make tf-validate   # terraform fmt check + validate
make deploy        # ENV=dev by default
make outputs       # Slack Request URL, runtime ARN, ...
```
