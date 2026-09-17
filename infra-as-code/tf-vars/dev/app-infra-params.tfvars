env          = "dev"
region       = "us-east-1"
project_name = "slack-agentcore"

model_id               = "us.amazon.nova-micro-v1:0"
linkedin_provider_name = "slack-agent-linkedin"
log_level              = "INFO"

default_tags = {
  project     = "slack-agentcore-app"
  environment = "dev"
  deployment  = "terraform"
}
