# S3 backend settings for the dev environment (passed with -backend-config).
# Replace the bucket with one you own; the key is unique per project/env.
bucket       = "use1-756375699536-terraform-state-bucket"
key          = "slack-agentcore-app/dev/terraform.tfstate"
region       = "us-east-1"
use_lockfile = true
