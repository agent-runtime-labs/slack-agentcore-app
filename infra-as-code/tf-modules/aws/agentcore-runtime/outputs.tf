output "agent_runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "agent_runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}

output "workload_identity_name" {
  value = local.workload_identity_name
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}
