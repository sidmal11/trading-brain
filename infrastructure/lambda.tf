# Lambda deployment and EventBridge scheduling for the Trading Brain poll handler.
#
# The packaging step (scripts/package_lambda.sh) must run before `terraform apply`
# so the zip at var.lambda_package_path exists. The script writes its output path
# to that same default location, so the default value here and the script stay in sync.
#
# EventBridge fires the function on a fixed rate (var.schedule_expression). The
# default is every 15 minutes, matching the lower bound in the spec. Raise it to
# "rate(30 minutes)" via tfvars if you want the wider window instead.

# ── Packaging ────────────────────────────────────────────────────────────────

# Terraform owns the hash check; the script produces the actual bytes. Using a
# local_file data source would force a read on every plan. Instead we expose the
# path and hash via variables so the packaging step is a required prerequisite
# rather than a silent Terraform-side download.

# ── Lambda function ───────────────────────────────────────────────────────────

resource "aws_lambda_function" "poll_handler" {
  function_name = "${var.project_name}-poll-handler"
  role          = aws_iam_role.lambda_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = var.lambda_runtime

  # Set by scripts/package_lambda.sh; terraform plan will fail clearly if absent.
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  memory_size = var.lambda_memory_mb
  timeout     = var.lambda_timeout_seconds

  # Environment variables read by config.py. Secrets Manager holds the sensitive
  # keys; non-sensitive config is wired directly so the function can start
  # without an extra GetSecretValue call for straightforward values.
  environment {
    variables = {
      DYNAMODB_TABLE_NAME           = aws_dynamodb_table.trading_alerts.name
      TELEGRAM_SUBSCRIBERS_TABLE    = aws_dynamodb_table.telegram_subscribers.name
      SECRET_NAME                   = aws_secretsmanager_secret.trading_secrets.name
      AWS_DEFAULT_REGION            = var.aws_region
      POLLING_INTERVAL_MINUTES      = tostring(var.polling_interval_minutes)
      STRATEGY_VOLUME_THRESHOLD     = tostring(var.strategy_volume_threshold)
      FEATURE_MULTI_AGENT_MODE      = "false"
      FEATURE_UI_DASHBOARD          = "true"
    }
  }

  # Attach to the log group that main.tf already manages so retention and tags
  # are controlled there and not duplicated here.
  logging_config {
    log_group             = aws_cloudwatch_log_group.lambda.name
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  # EventBridge and DynamoDB must exist before the invocation can succeed.
  depends_on = [
    aws_iam_role_policy.lambda_permissions,
    aws_cloudwatch_log_group.lambda,
    aws_dynamodb_table.trading_alerts,
    aws_secretsmanager_secret.trading_secrets,
  ]

  tags = {
    Name        = "${var.project_name}-poll-handler"
    Project     = "trading-brain"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

# ── EventBridge (CloudWatch Events) rule ─────────────────────────────────────

resource "aws_cloudwatch_event_rule" "poll_schedule" {
  name                = "${var.project_name}-poll-schedule"
  description         = "Fires the Trading Brain poll handler on a fixed interval to scan for Volume Breakthrough and RS leaders"
  schedule_expression = var.schedule_expression
  state               = "ENABLED"

  tags = {
    Name        = "${var.project_name}-poll-schedule"
    Project     = "trading-brain"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "poll_handler" {
  rule      = aws_cloudwatch_event_rule.poll_schedule.name
  target_id = "TradingBrainPollHandler"
  arn       = aws_lambda_function.poll_handler.arn
}

# Grants EventBridge the right to invoke the function. Without this the rule
# fires but the invocation is rejected with AccessDenied before reaching the handler.
resource "aws_lambda_permission" "eventbridge_invoke_poll_handler" {
  statement_id  = "AllowEventBridgeToInvokePollHandler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.poll_handler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.poll_schedule.arn
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "lambda_function_name" {
  description = "Name of the poll handler Lambda function"
  value       = aws_lambda_function.poll_handler.function_name
}

output "lambda_function_arn" {
  description = "ARN of the poll handler Lambda function"
  value       = aws_lambda_function.poll_handler.arn
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge schedule rule"
  value       = aws_cloudwatch_event_rule.poll_schedule.name
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge schedule rule"
  value       = aws_cloudwatch_event_rule.poll_schedule.arn
}
