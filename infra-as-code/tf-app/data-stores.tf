################################################################################
# Queue, DynamoDB tables and Slack secret
################################################################################

resource "aws_sqs_queue" "processing_dlq" {
  name                      = "${local.name_prefix}-processing-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "processing" {
  name                        = "${local.name_prefix}-processing.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  deduplication_scope         = "messageGroup"
  fifo_throughput_limit       = "perMessageGroupId"
  # Must exceed the worker Lambda timeout.
  visibility_timeout_seconds = 900
  message_retention_seconds  = 86400
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dlq.arn
    maxReceiveCount     = 2
  })
}

# Short-lived OAuth session-binding records (nonce -> Slack user + session URI).
resource "aws_dynamodb_table" "pending_auth" {
  name         = "${local.name_prefix}-pending-oauth"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "nonce"

  attribute {
    name = "nonce"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# Slack threads the bot has replied in (team:channel:thread_ts), so follow-ups there are
# answered without an @mention. Items carry their own expiry; see engaged_threads.py.
resource "aws_dynamodb_table" "engaged_threads" {
  name         = "${local.name_prefix}-engaged-threads"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "thread_key"

  attribute {
    name = "thread_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# Per-user OAuth tokens for CIMD providers (Linear, Notion, ...). With AgentCore
# Identity, AWS owns the token vault; a CIMD client has none, so this table is it.
# Written by the oauth_callback Lambda when a user consents, read and refreshed by the
# agent runtime. Schema: docs/cimd-providers.md.
resource "aws_dynamodb_table" "cimd_tokens" {
  name         = "${local.name_prefix}-cimd-tokens"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "provider"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "provider"
    type = "S"
  }

  # Expires a whole connection after cimd_connection_ttl_days of inactivity, so an
  # abandoned user's refresh token does not live forever. Deliberately not the access
  # token's expiry -- that would delete the refresh token an hour after consent.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    # Tokens are re-obtainable by reconnecting; a stale restored token is worse than none.
    enabled = false
  }
}

# The value is written out-of-band (scripts/put-slack-secret.sh) so the Slack
# tokens never land in Terraform state.
resource "aws_secretsmanager_secret" "slack" {
  name                    = "${local.name_prefix}/slack"
  description             = "Slack bot token and signing secret: {\"bot_token\":..., \"signing_secret\":...}"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
}
