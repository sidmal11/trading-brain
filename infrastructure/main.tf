output "dynamodb_table_name" {
  description = "DynamoDB table name for trading alerts"
  value       = aws_dynamodb_table.trading_alerts.name
}

output "iam_role_name" {
  description = "Name of the IAM role for Lambda"
  value       = aws_iam_role.lambda_role.name
}

output "secret_name" {
  description = "Name of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.trading_secrets.name
}
