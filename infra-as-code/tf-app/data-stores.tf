################################################################################
# Queue, pending-OAuth table and Slack secret
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

# The value is written out-of-band (scripts/put-slack-secret.sh) so the Slack
# tokens never land in Terraform state.
resource "aws_secretsmanager_secret" "slack" {
  name                    = "${local.name_prefix}/slack"
  description             = "Slack bot token and signing secret: {\"bot_token\":..., \"signing_secret\":...}"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
}
