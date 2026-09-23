output "slack_events_url" {
  description = "Paste into Slack app > Event Subscriptions > Request URL"
  value       = "${local.public_base_url}/slack/events"
}

output "oauth_callback_url" {
  description = "Return URL allow-listed on the runtime's workload identity"
  value       = local.oauth_callback_url
}

output "cimd_client_id" {
  description = "Our OAuth client_id for CIMD providers: the URL of the client metadata document. Open it in a browser to check what authorization servers see."
  value       = local.cimd_client_id
}

output "cimd_token_table" {
  description = "Per-user tokens for CIMD providers (Linear, Notion, ...)"
  value       = aws_dynamodb_table.cimd_tokens.name
}

output "agent_runtime_arn" {
  value = module.agent_runtime.agent_runtime_arn
}

output "workload_identity_name" {
  value = module.agent_runtime.workload_identity_name
}

output "slack_secret_arn" {
  description = "Populate with scripts/put-slack-secret.sh"
  value       = aws_secretsmanager_secret.slack.arn
}

output "processing_dlq_url" {
  value = aws_sqs_queue.processing_dlq.url
}
