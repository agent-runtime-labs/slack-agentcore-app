mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
}

variables {
  name                             = "policy_test"
  image_uri                        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/test:latest"
  ecr_repository_arn               = "arn:aws:ecr:us-east-1:123456789012:repository/test"
  model_resource_arns              = ["arn:aws:bedrock:*::foundation-model/test"]
  oauth2_credential_provider_names = []
}

run "mixed_statement_shapes" {
  command = plan

  variables {
    additional_policy_statements = [
      {
        Sid      = "ExistingTokenVault"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = "arn:aws:dynamodb:us-east-1:123456789012:table/existing"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:us-east-1:123456789012:secret:kubernetes-test"]
        Condition = {
          StringEquals = { "aws:ResourceTag/environment" = "test" }
        }
      },
    ]
  }

  assert {
    condition = slice(
      jsondecode(aws_iam_role_policy.execution.policy).Statement,
      length(jsondecode(aws_iam_role_policy.execution.policy).Statement) - 2,
      length(jsondecode(aws_iam_role_policy.execution.policy).Statement)
    ) == var.additional_policy_statements
    error_message = "Preserve optional Sid/Condition and scalar/list Resource values exactly in the rendered IAM policy."
  }
}

run "empty_statements" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.execution.policy).Statement) > 0
    error_message = "The default empty extension must retain the module's base IAM policy."
  }
}

run "reject_non_sequence" {
  command = plan

  variables {
    additional_policy_statements = "invalid"
  }

  expect_failures = [var.additional_policy_statements]
}
