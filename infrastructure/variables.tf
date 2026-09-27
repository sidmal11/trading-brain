variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "table_name" {
  description = "Name of the DynamoDB table for trading alerts"
  type        = string
  default     = "TradingAlerts"
}

variable "secret_name" {
  description = "Name of the Secrets Manager secret"
  type        = string
  default     = "trading-brain-secrets"
}
