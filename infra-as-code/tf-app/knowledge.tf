################################################################################
# Team knowledge memory (knowledge_enabled): summaries of earlier Slack threads in
# S3 Vectors, kept up to date by the knowledge-indexer Lambda and searched by the agent's
# search_past_threads tool. See docs/architecture.md ("Team knowledge memory").
# Everything here has count = 0 while the feature is off.
################################################################################

locals {
  knowledge_count = var.knowledge_enabled ? 1 : 0
  knowledge_index = "threads"

  knowledge_bucket    = one(aws_s3vectors_vector_bucket.knowledge[*].vector_bucket_name)
  knowledge_index_arn = one(aws_s3vectors_index.threads[*].index_arn)
  knowledge_queue_arn = one(aws_sqs_queue.knowledge_index[*].arn)
  knowledge_queue_url = one(aws_sqs_queue.knowledge_index[*].url)

  # Titan is invoked on-demand, not through an inference profile.
  knowledge_embedding_model_arn = "arn:aws:bedrock:${var.region}::foundation-model/${var.knowledge_embedding_model_id}"

  # Lambda environments get these only when the feature is on, so turning it off leaves
  # them exactly as they were before.
  knowledge_events_env = var.knowledge_enabled ? {
    KNOWLEDGE_ENABLED           = "true"
    KNOWLEDGE_QUEUE_URL         = local.knowledge_queue_url
    KNOWLEDGE_EXCLUDED_CHANNELS = join(",", var.knowledge_excluded_channels)
  } : {}
  knowledge_worker_env = var.knowledge_enabled ? { KNOWLEDGE_ENABLED = "true" } : {}
  knowledge_agent_env = var.knowledge_enabled ? {
    KNOWLEDGE_VECTOR_BUCKET      = local.knowledge_bucket
    KNOWLEDGE_INDEX              = local.knowledge_index
    KNOWLEDGE_EMBEDDING_MODEL_ID = var.knowledge_embedding_model_id
  } : {}

  # slack_events only queues work for the indexer: no Slack reads, no model calls.
  knowledge_events_statements = var.knowledge_enabled ? [{
    Effect   = "Allow"
    Action   = ["sqs:SendMessage"]
    Resource = local.knowledge_queue_arn
  }] : []

  # Read-only for the agent: a query that returns metadata needs GetVectors as well.
  knowledge_agent_statements = var.knowledge_enabled ? [
    {
      Sid      = "SearchPastThreads"
      Effect   = "Allow"
      Action   = ["s3vectors:QueryVectors", "s3vectors:GetVectors"]
      Resource = local.knowledge_index_arn
    },
    {
      Sid      = "EmbedSearchQueries"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = local.knowledge_embedding_model_arn
    },
  ] : []
}

resource "aws_s3vectors_vector_bucket" "knowledge" {
  count = local.knowledge_count

  vector_bucket_name = "${local.name_prefix}-knowledge"
  # Summaries can be rebuilt from Slack as threads are picked up again.
  force_destroy = var.env != "prod"
}

resource "aws_s3vectors_index" "threads" {
  count = local.knowledge_count

  vector_bucket_name = aws_s3vectors_vector_bucket.knowledge[0].vector_bucket_name
  index_name         = local.knowledge_index
  data_type          = "float32"
  dimension          = 1024 # Titan Text Embeddings v2
  distance_metric    = "cosine"

  # Stored with each vector for citing a thread, never filtered on. Every other key
  # (team_id, channel_id, thread_key, visibility, updated_at, status, kind,
  # schema_version) stays filterable.
  metadata_configuration {
    non_filterable_metadata_keys = ["summary", "permalink", "channel_name", "participants", "last_message_ts"]
  }
}

resource "aws_sqs_queue" "knowledge_index_dlq" {
  count = local.knowledge_count

  name                      = "${local.name_prefix}-knowledge-index-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

# Standard, not FIFO: each check is sent with DelaySeconds (the quiet period), which FIFO
# queues only support per queue. Order doesn't matter, since a check re-reads the thread.
resource "aws_sqs_queue" "knowledge_index" {
  count = local.knowledge_count

  name = "${local.name_prefix}-knowledge-index"
  # Six times the indexer's timeout, as AWS recommends for Lambda consumers.
  visibility_timeout_seconds = 3600
  message_retention_seconds  = 86400
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.knowledge_index_dlq[0].arn
    maxReceiveCount     = 3
  })
}

module "knowledge_indexer_fn" {
  source = "../tf-modules/aws/lambda-function"
  count  = local.knowledge_count

  name        = "${local.name_prefix}-knowledge-indexer"
  description = "Summarises quiet Slack threads into the team knowledge index, and sweeps it daily"
  image_uri   = module.lambda_image.image_uri
  command     = ["slack_app.handlers.knowledge_indexer.handler"]
  # Up to 5 jobs a batch, each maybe a Sonnet call with thinking over a long thread, and
  # the fallback model's call if Sonnet can't be used.
  timeout     = 600
  memory_size = 512

  environment_variables = merge(local.lambda_common_env, {
    KNOWLEDGE_ENABLED                   = "true"
    KNOWLEDGE_VECTOR_BUCKET             = local.knowledge_bucket
    KNOWLEDGE_INDEX                     = local.knowledge_index
    KNOWLEDGE_SUMMARY_MODEL_ID          = var.knowledge_summary_model_id
    KNOWLEDGE_SUMMARY_FALLBACK_MODEL_ID = var.knowledge_summary_fallback_model_id
    KNOWLEDGE_EMBEDDING_MODEL_ID        = var.knowledge_embedding_model_id
    KNOWLEDGE_EXCLUDED_CHANNELS         = join(",", var.knowledge_excluded_channels)
  })

  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.read_slack_secret,
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = local.knowledge_queue_arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"]
        Resource = local.knowledge_index_arn
      },
      {
        # One summary call per quiet thread (the fallback model if the summary model can't
        # be used), then one Titan call per stored vector.
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = concat(
          local.model_arns_by_id[var.knowledge_summary_model_id],
          lookup(local.model_arns_by_id, var.knowledge_summary_fallback_model_id, []),
          [local.knowledge_embedding_model_arn],
        )
      },
    ]
  })
}

resource "aws_lambda_event_source_mapping" "knowledge_indexer" {
  count = local.knowledge_count

  event_source_arn        = aws_sqs_queue.knowledge_index[0].arn
  function_name           = module.knowledge_indexer_fn[0].function_arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]

  # Keeps Slack's conversations.replies and Bedrock well inside their rate limits.
  scaling_config {
    maximum_concurrency = 5
  }
}

# Daily sweep: drops channels the bot left or that became Slack Connect, and rewrites
# the visibility tag of channels that switched between public and private.
resource "aws_cloudwatch_event_rule" "knowledge_sweep" {
  count = local.knowledge_count

  name                = "${local.name_prefix}-knowledge-sweep"
  description         = "Daily check of every channel in the team knowledge index"
  schedule_expression = "rate(1 day)"
}

resource "aws_cloudwatch_event_target" "knowledge_sweep" {
  count = local.knowledge_count

  rule  = aws_cloudwatch_event_rule.knowledge_sweep[0].name
  arn   = module.knowledge_indexer_fn[0].function_arn
  input = jsonencode({ sweep = true })
}

resource "aws_lambda_permission" "knowledge_sweep" {
  count = local.knowledge_count

  statement_id  = "AllowDailySweep"
  action        = "lambda:InvokeFunction"
  function_name = module.knowledge_indexer_fn[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.knowledge_sweep[0].arn
}
