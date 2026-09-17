output "image_uri" {
  description = "Pushed image URI (depends on the build, so consumers wait for the push)"
  value       = local.image_uri
  depends_on  = [terraform_data.build_and_push]
}

output "repository_arn" {
  value = aws_ecr_repository.this.arn
}

output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}
