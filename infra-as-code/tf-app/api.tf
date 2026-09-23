################################################################################
# Public HTTP API (Slack webhook + OAuth browser endpoints)
################################################################################

resource "aws_apigatewayv2_api" "this" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit
  }

  # No query strings or bodies are logged (the OAuth nonce travels in the query).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      time      = "$context.requestTime"
      method    = "$context.httpMethod"
      route     = "$context.routeKey"
      status    = "$context.status"
      latency   = "$context.integrationLatency"
      error     = "$context.integrationErrorMessage"
    })
  }
}

locals {
  route_functions = {
    slack_events   = module.slack_events_fn
    oauth_callback = module.oauth_callback_fn
  }
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each = local.route_functions

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = each.value.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "this" {
  for_each = {
    "POST /slack/events"   = "slack_events"
    "GET /oauth2/start"    = "oauth_callback"
    "GET /oauth2/callback" = "oauth_callback"
    # Our CIMD client_id. Authorization servers fetch it unauthenticated, from their
    # own infrastructure, so it must stay public.
    "GET /oauth2/client-metadata.json" = "oauth_callback"
  }

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.fn[each.value].id}"
}

resource "aws_lambda_permission" "api" {
  for_each = local.route_functions

  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = each.value.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
