resource "aws_dynamodb_table" "trading_alerts" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alertId"

  attribute {
    name = "alertId"
    type = "S"
  }

  # The Lambda reads and writes alerts only; it never needs to scan the table,
  # so there is deliberately no range key and no GSI.
  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = var.table_name
    Project     = "trading-brain"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_role" {
  name               = "${var.project_name}-lambda-role"
  description        = "Execution role for the Trading Brain Lambda poll handler"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name      = "${var.project_name}-lambda-role"
    ManagedBy = "terraform"
  }
}

data "aws_iam_policy_document" "lambda_permissions" {
  statement {
    sid    = "TradingAlertsTableAccess"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]

    resources = [aws_dynamodb_table.trading_alerts.arn]
  }

  statement {
    sid    = "ReadTradingSecrets"
    effect = "Allow"

    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.trading_secrets.arn]
  }

  statement {
    sid       = "WriteLambdaLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  # Interface management for a function running inside a VPC. This grants no
  # network egress of its own; outbound access comes from the subnet's route
  # table or a NAT gateway, not from IAM.
  dynamic "statement" {
    for_each = var.lambda_vpc_ids

    content {
      sid    = "ManageInterfacesInsideVpc"
      effect = "Allow"

      actions   = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface"]
      resources = ["*"]
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-poll-handler"
  retention_in_days = var.log_retention_days

  tags = {
    Name      = "${var.project_name}-poll-handler"
    ManagedBy = "terraform"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "lambda_permissions" {
  name   = "${var.project_name}-lambda-permissions"
  role   = aws_iam_role.lambda_role.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

resource "aws_secretsmanager_secret" "trading_secrets" {
  name                    = var.secret_name
  description             = "API keys and endpoints for the Trading Brain pipeline"
  recovery_window_in_days = 7

  tags = {
    Name      = var.secret_name
    ManagedBy = "terraform"
  }
}

output "dynamodb_table_name" {
  description = "DynamoDB table name for trading alerts"
  value       = aws_dynamodb_table.trading_alerts.name
}

output "iam_role_name" {
  description = "Name of the IAM role for Lambda"
  value       = aws_iam_role.lambda_role.name
}

output "iam_role_arn" {
  description = "ARN of the IAM role for Lambda"
  value       = aws_iam_role.lambda_role.arn
}

output "secret_name" {
  description = "Name of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.trading_secrets.name
}

output "secret_arn" {
  description = "ARN of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.trading_secrets.arn
}

output "table_arn" {
  description = "ARN of the trading alerts table"
  value       = aws_dynamodb_table.trading_alerts.arn
}
