################################################################################
# Per-user memory (user_memory_enabled): what each person told the bot about themselves,
# in AgentCore Memory, kept only for people who said yes (the memory-consent table).
# The agent saves and reads it (user_memory.py); the Lambdas ask for consent, decide
# what may be used where, and delete it on request (slack_app/user_memory/).
# Everything here has count = 0 while the feature is off. The slack-button-clicks Lambda
# (lambdas.tf) is always deployed, and only gets access to the consent table with it on.
################################################################################

locals {
  user_memory_count = var.user_memory_enabled ? 1 : 0

  user_memory_id       = one(aws_bedrockagentcore_memory.user[*].id)
  user_memory_arn      = one(aws_bedrockagentcore_memory.user[*].arn)
  memory_consent_table = one(aws_dynamodb_table.memory_consent[*].name)
  memory_consent_arn   = one(aws_dynamodb_table.memory_consent[*].arn)

  user_memory_agent_env = var.user_memory_enabled ? { USER_MEMORY_ID = local.user_memory_id } : {}
  user_memory_worker_env = var.user_memory_enabled ? {
    USER_MEMORY_ENABLED  = "true"
    USER_MEMORY_ID       = local.user_memory_id
    MEMORY_CONSENT_TABLE = local.memory_consent_table
  } : {}

  # slack-button-clicks (lambdas.tf) records the choice and queues the deletion.
  user_memory_clicks_env = var.user_memory_enabled ? {
    USER_MEMORY_ENABLED  = "true"
    MEMORY_CONSENT_TABLE = local.memory_consent_table
    PROCESSING_QUEUE_URL = aws_sqs_queue.processing.url
  } : {}
  user_memory_clicks_statements = var.user_memory_enabled ? [
    {
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
      Resource = local.memory_consent_arn
    },
    {
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.processing.arn
    },
  ] : []

  # The agent saves a turn and reads records back. It can't delete, and it never sees
  # the consent table: the worker tells it what the person agreed to.
  user_memory_agent_statements = var.user_memory_enabled ? [{
    Sid      = "UserMemory"
    Effect   = "Allow"
    Action   = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"]
    Resource = local.user_memory_arn
  }] : []

  # The worker reads consent on every request, notes the tiers a person has memory in,
  # and deletes a person's memory when they turn it off. It never reads what was kept.
  user_memory_worker_statements = var.user_memory_enabled ? [
    {
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
      Resource = local.memory_consent_arn
    },
    {
      Effect = "Allow"
      Action = [
        "bedrock-agentcore:ListSessions",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:DeleteEvent",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:BatchDeleteMemoryRecords",
      ]
      Resource = local.user_memory_arn
    },
  ] : []
}

resource "aws_bedrockagentcore_memory" "user" {
  count = local.user_memory_count

  # Letters, digits and underscores only, like the runtime's name.
  name        = "${local.runtime_name}_user_memory"
  description = "What each Slack user let the assistant remember about them"
  # Raw turns are only the feed for extraction, so they don't need to live long.
  event_expiry_duration = var.user_memory_event_expiry_days
}

# One actor per person and tier (slack-<team>-<user>-<tier>), so what was said in a DM is
# never in the same namespace as what was said in a channel. The namespaces must match
# user_memory.py in the agent and slack_app/user_memory/__init__.py in the Lambdas.
resource "aws_bedrockagentcore_memory_strategy" "preferences" {
  count = local.user_memory_count

  name                = "user_preferences"
  description         = "How a person likes answers, and the tools and defaults they use"
  memory_id           = aws_bedrockagentcore_memory.user[0].id
  type                = "USER_PREFERENCE"
  namespace_templates = ["/users/{actorId}/preferences/"]
}

resource "aws_bedrockagentcore_memory_strategy" "facts" {
  count = local.user_memory_count

  name                = "user_facts"
  description         = "What a person said about themselves: their role, team and what they own"
  memory_id           = aws_bedrockagentcore_memory.user[0].id
  type                = "SEMANTIC"
  namespace_templates = ["/users/{actorId}/facts/"]

  # AgentCore changes one strategy of a memory at a time.
  depends_on = [aws_bedrockagentcore_memory_strategy.preferences]
}

# Each person's answer to "may I remember you?" (slack_app/user_memory/consent.py).
resource "aws_dynamodb_table" "memory_consent" {
  count = local.user_memory_count

  name         = "${local.name_prefix}-memory-consent"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    # Losing this table would lose who said no.
    enabled = true
  }
}
